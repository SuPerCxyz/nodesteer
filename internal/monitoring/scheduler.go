package monitoring

import (
	"context"
	"encoding/json"
	"errors"
	"log/slog"
	"net/http"
	"net/url"
	"os"
	"sort"
	"strings"
	"sync"
	"time"
)

// Scheduler runs configured probes using a bounded number of workers.
type Scheduler struct {
	repo         *Repository
	transport    http.RoundTripper
	network      *NetworkPolicy
	logger       *slog.Logger
	trigger      func(context.Context, Rule, string) error
	remote       func(context.Context, Target) (HTTPObservation, error)
	nodeMetric   func(context.Context, Target) (HTTPObservation, error)
	actions      sync.WaitGroup
	actionSlots  chan struct{}
	workers      int
	tickInterval time.Duration
}

func NewScheduler(repo *Repository, transport http.RoundTripper, logger *slog.Logger) *Scheduler {
	return &Scheduler{repo: repo, transport: transport, logger: logger, actionSlots: make(chan struct{}, 8), workers: 8, tickInterval: time.Second}
}

// SetWorkers bounds simultaneous collection; configure before Run.
func (s *Scheduler) SetWorkers(workers int) error {
	if workers < 1 || workers > 128 {
		return errors.New("monitoring workers must be between 1 and 128")
	}
	s.workers = workers
	return nil
}

func (s *Scheduler) SetTrigger(trigger func(context.Context, Rule, string) error) {
	s.trigger = trigger
}

func (s *Scheduler) SetNetwork(policy *NetworkPolicy) { s.network = policy }

func (s *Scheduler) SetRemoteProbe(probe func(context.Context, Target) (HTTPObservation, error)) {
	s.remote = probe
}

func (s *Scheduler) SetNodeMetricProbe(probe func(context.Context, Target) (HTTPObservation, error)) {
	s.nodeMetric = probe
}

func (s *Scheduler) Run(ctx context.Context) {
	if err := s.repo.MarkPendingUnknown(ctx); err != nil {
		s.logger.Error("monitoring recovery failed", "error", err)
		return
	}
	ticker := time.NewTicker(s.tickInterval)
	defer ticker.Stop()
	retention := time.NewTicker(15 * time.Minute)
	defer retention.Stop()
	last := map[string]time.Time{}
	inflight := map[string]bool{}
	done := make(chan string, s.workers)
	workers := make(chan struct{}, s.workers)
	var lastOverload time.Time
	var running sync.WaitGroup
	defer func() { running.Wait(); s.actions.Wait() }()
	for {
		select {
		case <-ctx.Done():
			return
		case now := <-retention.C:
			if err := s.repo.PruneObservations(ctx, now); err != nil && ctx.Err() == nil {
				s.logger.Error("monitoring retention failed", "error", err)
			}
		case id := <-done:
			delete(inflight, id)
		case now := <-ticker.C:
			targets, err := s.repo.List(ctx)
			if err != nil {
				s.logger.Error("monitoring targets read failed", "error", err)
				continue
			}
			sort.SliceStable(targets, func(i, j int) bool {
				left, right := last[targets[i].ID], last[targets[j].ID]
				if left.IsZero() || right.IsZero() {
					return left.IsZero() && !right.IsZero()
				}
				return left.Add(time.Duration(targets[i].IntervalSec) * time.Second).Before(right.Add(time.Duration(targets[j].IntervalSec) * time.Second))
			})
			deferred := 0
			for _, target := range targets {
				if !target.Enabled || inflight[target.ID] || now.Sub(last[target.ID]) < time.Duration(target.IntervalSec)*time.Second {
					continue
				}
				select {
				case workers <- struct{}{}:
					last[target.ID], inflight[target.ID] = now, true
					running.Add(1)
					go func(target Target) {
						defer running.Done()
						defer func() {
							<-workers
							select {
							case done <- target.ID:
							case <-ctx.Done():
							}
						}()
						s.observe(ctx, target)
					}(target)
				default:
					deferred++
				}
			}
			if deferred > 0 && now.Sub(lastOverload) >= time.Minute {
				s.logger.Warn("monitoring collection overloaded; due targets delayed", "deferred", deferred, "workers", s.workers)
				lastOverload = now
			}
		}
	}
}

func (s *Scheduler) observe(ctx context.Context, target Target) {
	var obs HTTPObservation
	var err error
	if target.Kind == "node_metric" {
		if s.nodeMetric == nil {
			err = errors.New("node metric monitoring is not configured")
		} else {
			obs, err = s.nodeMetric(ctx, target)
		}
	} else if target.SourceNodeID != "" {
		if s.remote == nil {
			err = errors.New("agent probe unavailable")
		} else {
			obs, err = s.remote(ctx, target)
		}
	} else {
		obs, err = Collect(ctx, target, s.transport, s.network)
	}
	current, getErr := s.repo.Get(ctx, target.ID)
	if getErr != nil || !current.Enabled {
		return
	}
	before, _ := json.Marshal(target)
	after, _ := json.Marshal(current)
	if string(before) != string(after) {
		return
	}
	if insertErr := s.repo.addObservation(ctx, target.ID, string(before), obs, err); insertErr != nil {
		if ctx.Err() == nil {
			s.logger.Error("monitoring observation write failed", "target_id", target.ID, "error", insertErr)
		}
		return
	}
	s.evaluate(ctx, target, obs, err)
}

func Collect(ctx context.Context, target Target, transport http.RoundTripper, network *NetworkPolicy) (HTTPObservation, error) {
	credential := ""
	secrets := []string{}
	var obs HTTPObservation
	var err error
	if target.CredentialEnv != "" {
		credential = os.Getenv(target.CredentialEnv)
		if credential == "" {
			err = errors.New("monitoring credential is not configured")
		} else {
			secrets = append(secrets, credential)
		}
	}
	if err == nil && (target.Kind == "tcp" || target.Kind == "icmp") {
		if network == nil {
			err = errors.New("network probe unavailable")
		} else {
			address, parseErr := url.Parse(target.URL)
			if parseErr != nil {
				err = errors.New("invalid network target")
			} else {
				obs, err = network.Observe(ctx, target.Kind, address.Hostname(), address.Port(), time.Duration(target.TimeoutSec)*time.Second)
			}
		}
	} else if err == nil {
		var targetURL *url.URL
		targetURL, err = url.Parse(target.URL)
		if err == nil {
			query := targetURL.Query()
			for _, param := range target.QueryParams {
				value := param.Value
				if param.SecretEnv != "" {
					value = os.Getenv(param.SecretEnv)
					if value == "" {
						err = errors.New("monitoring query credential is not configured")
						break
					}
					secrets = append(secrets, value)
				}
				query.Add(param.Name, value)
			}
			targetURL.RawQuery = query.Encode()
		}
		if err == nil {
			obs, err = observeHTTP(ctx, HTTPProbe{URL: targetURL.String(), JSONPath: target.JSONPath, Credential: credential, Timeout: time.Duration(target.TimeoutSec) * time.Second}, transport)
		}
	}
	if obs.HasValue {
		data, _ := json.Marshal(obs.Value)
		value := string(data)
		if str, ok := obs.Value.(string); ok {
			value = str
		}
		for _, secret := range secrets {
			if secret != "" && strings.Contains(value, secret) {
				obs.Value, obs.HasValue = nil, false
				err = errors.New("monitoring response contains credential")
				break
			}
		}
	}
	return obs, err
}

func (s *Scheduler) evaluate(ctx context.Context, target Target, obs HTTPObservation, err error) {
	if s.trigger == nil {
		return
	}
	rules, listErr := s.repo.Rules(ctx, target.ID)
	if listErr != nil {
		s.logger.Error("monitoring rules read failed", "target_id", target.ID, "error", listErr)
		return
	}
	for _, rule := range rules {
		if !rule.Enabled {
			continue
		}
		if target.Kind == "node_metric" && err == nil {
			acquired := false
			select {
			case s.actionSlots <- struct{}{}:
				acquired = true
			default:
			}
			claim, claimErr := s.repo.ProcessNodeSamples(ctx, rule, target, obs, acquired)
			if claimErr != nil {
				if acquired {
					<-s.actionSlots
				}
				if resetErr := s.repo.ResetRuleDuration(ctx, rule.ID); resetErr != nil && ctx.Err() == nil {
					s.logger.Error("monitoring rule duration reset failed", "rule_id", rule.ID, "error", resetErr)
				}
				if ctx.Err() == nil {
					s.logger.Error("monitoring sample batch failed", "rule_id", rule.ID, "error", claimErr)
				}
				continue
			}
			if claim == "" {
				if acquired {
					<-s.actionSlots
				}
				continue
			}
			s.submitClaim(ctx, rule, claim)
			continue
		}
		if rule.Operator == "probe_error" && err != nil && !errors.Is(err, ErrProbeUnreachable) {
			if resetErr := s.repo.ResetRuleDuration(ctx, rule.ID); resetErr != nil && ctx.Err() == nil {
				s.logger.Error("monitoring rule duration reset failed", "rule_id", rule.ID, "error", resetErr)
			}
			continue
		}
		if rule.Operator != "probe_error" && err != nil && (rule.Operator != "status_eq" || obs.StatusCode == 0) {
			if resetErr := s.repo.ResetRuleDuration(ctx, rule.ID); resetErr != nil && ctx.Err() == nil {
				s.logger.Error("monitoring rule duration reset failed", "rule_id", rule.ID, "error", resetErr)
			}
			continue
		}
		var matched bool
		var compareErr error
		if rule.Operator == "probe_error" {
			matched = errors.Is(err, ErrProbeUnreachable)
		} else {
			maxAge := time.Duration(target.IntervalSec*2) * time.Second
			if target.Kind == "node_metric" {
				maxAge = target.MetricMaxAge()
			}
			matched, compareErr = CompareHTTP(obs, time.Now(), maxAge, rule.Operator, rule.Expected)
		}
		if compareErr != nil {
			if resetErr := s.repo.ResetRuleDuration(ctx, rule.ID); resetErr != nil && ctx.Err() == nil {
				s.logger.Error("monitoring rule duration reset failed", "rule_id", rule.ID, "error", resetErr)
			}
			continue
		}
		acquired := false
		select {
		case s.actionSlots <- struct{}{}:
			acquired = true
		default:
		}
		claim, claimErr := s.repo.claimRule(ctx, rule, target, matched, obs.ObservedAt, time.Duration(target.IntervalSec*2)*time.Second, acquired, obs)
		if claimErr != nil {
			if acquired {
				<-s.actionSlots
			}
			s.logger.Error("monitoring rule claim failed", "rule_id", rule.ID, "error", claimErr)
			continue
		}
		if claim == "" {
			if acquired {
				<-s.actionSlots
			}
			continue
		}
		s.submitClaim(ctx, rule, claim)
	}
}

func (s *Scheduler) submitClaim(ctx context.Context, rule Rule, claim string) {
	s.actions.Add(1)
	go func() {
		defer s.actions.Done()
		defer func() { <-s.actionSlots }()
		triggerErr := s.trigger(ctx, rule, claim)
		if triggerErr != nil && ctx.Err() == nil {
			// Raw action errors stay in logs; the event stores only a
			// normalized message so storage details are not exposed.
			s.logger.Error("monitoring action failed", "rule_id", rule.ID, "error", triggerErr)
		}
		if finishErr := s.repo.FinishRule(ctx, rule.ID, claim, triggerErr); finishErr != nil && ctx.Err() == nil {
			s.logger.Error("monitoring rule result write failed", "rule_id", rule.ID, "error", finishErr)
		}
	}()
}
