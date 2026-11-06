package execution

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"os/user"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"syscall"
	"time"
)

// RunConfig 运行配置
type RunConfig struct {
	Command     string
	Script      string
	Interpreter string // shell | bash | python
	WorkingDir  string
	Environment map[string]string
	RunUser     string
	Timeout     time.Duration
	// 日志限制
	MaxStdoutBytes int
	MaxStderrBytes int
	MaxTotalBytes  int
	// OnLogChunk 实时日志回调（可选）：执行期间 stdout/stderr 分片，stream 为 "stdout" 或 "stderr"
	OnLogChunk func(stream string, chunk []byte)
}

// Result 运行结果
type Result struct {
	ExitCode        int
	Stdout          string
	Stderr          string
	StdoutTruncated bool
	StderrTruncated bool
	Status          string // SUCCESS | FAILED | TIMED_OUT | CANCELED
	TimedOut        bool
	Canceled        bool
}

// Runner 进程执行器
type Runner struct {
	mu      sync.Mutex
	running map[string]context.CancelFunc
}

// New 创建执行器
func New() *Runner {
	return &Runner{running: map[string]context.CancelFunc{}}
}

// Cancel 取消执行
func (r *Runner) Cancel(execID string) {
	r.mu.Lock()
	cancel, ok := r.running[execID]
	r.mu.Unlock()
	if ok {
		cancel()
	}
}

// Run 执行命令/脚本
func (r *Runner) Run(ctx context.Context, execID string, cfg RunConfig) (*Result, error) {
	ctx, cancel := context.WithCancel(ctx)
	r.mu.Lock()
	r.running[execID] = cancel
	r.mu.Unlock()
	defer func() {
		r.mu.Lock()
		delete(r.running, execID)
		r.mu.Unlock()
		cancel()
	}()

	cmd, err := buildCmd(ctx, cfg)
	if err != nil {
		return &Result{Status: "FAILED", ExitCode: -1}, err
	}

	// stdout/stderr 输出（限制大小 + 实时分片回调）。
	// 使用 cmd.Stdout/cmd.Stderr 自定义 writer：os/exec 内部通过 pipe+goroutine 拷贝，
	// 并在 Wait() 中等待拷贝完成，避免 StdoutPipe+异步读 的 fd 关闭竞态导致输出丢失。
	var outBuf, errBuf limitedBuffer
	if cfg.MaxStdoutBytes <= 0 {
		cfg.MaxStdoutBytes = 512 << 10
	}
	if cfg.MaxStderrBytes <= 0 {
		cfg.MaxStderrBytes = 512 << 10
	}
	if cfg.MaxTotalBytes <= 0 {
		cfg.MaxTotalBytes = 1 << 20
	}
	// 硬上限：即使配置更大也不让单次执行结果超过 1MiB（每流 512KiB）。
	if cfg.MaxStdoutBytes > 512<<10 {
		cfg.MaxStdoutBytes = 512 << 10
	}
	if cfg.MaxStderrBytes > 512<<10 {
		cfg.MaxStderrBytes = 512 << 10
	}
	if cfg.MaxTotalBytes > 1<<20 {
		cfg.MaxTotalBytes = 1 << 20
	}
	totalUsed := int64(0)
	outBuf.limit = int64(cfg.MaxStdoutBytes)
	errBuf.limit = int64(cfg.MaxStderrBytes)
	outBuf.totalLimit = int64(cfg.MaxTotalBytes)
	outBuf.total = &totalUsed
	errBuf.total = &totalUsed

	cmd.Stdout = &streamWriter{buf: &outBuf, stream: "stdout", onChunk: cfg.OnLogChunk}
	cmd.Stderr = &streamWriter{buf: &errBuf, stream: "stderr", onChunk: cfg.OnLogChunk}

	if err := cmd.Start(); err != nil {
		return &Result{Status: "FAILED", ExitCode: -1}, err
	}

	// 等待完成或超时
	done := make(chan error, 1)
	go func() { done <- cmd.Wait() }()

	var timedOut, canceled bool
	var waitErr error
	select {
	case waitErr = <-done:
	case <-time.After(cfg.Timeout):
		timedOut = true
		killProcessGroup(cmd)
		waitErr = <-done
	case <-ctx.Done():
		canceled = true
		killProcessGroup(cmd)
		waitErr = <-done
	}
	// os/exec 在 Wait() 返回前已 join 内部 stdout/stderr 拷贝 goroutine，输出完整无竞态

	res := &Result{
		Stdout:          outBuf.String(),
		Stderr:          errBuf.String(),
		StdoutTruncated: outBuf.truncated,
		StderrTruncated: errBuf.truncated,
		TimedOut:        timedOut,
		Canceled:        canceled,
	}
	if timedOut {
		res.Status = "TIMED_OUT"
		res.ExitCode = -1
		return res, nil
	}
	if canceled {
		res.Status = "CANCELED"
		res.ExitCode = -1
		return res, nil
	}
	if waitErr != nil {
		var ee *exec.ExitError
		if errors.As(waitErr, &ee) {
			res.ExitCode = ee.ExitCode()
			if res.ExitCode == 0 {
				res.ExitCode = -1
			}
			res.Status = "FAILED"
			return res, nil
		}
		res.ExitCode = -1
		res.Status = "FAILED"
		return res, waitErr
	}
	res.ExitCode = 0
	res.Status = "SUCCESS"
	return res, nil
}

// buildCmd 构造命令（进程组 + run user）
func buildCmd(ctx context.Context, cfg RunConfig) (*exec.Cmd, error) {
	var cmd *exec.Cmd
	switch {
	case cfg.Command != "":
		cmd = exec.CommandContext(ctx, "/bin/sh", "-c", cfg.Command)
	case cfg.Script != "":
		interp := cfg.Interpreter
		switch interp {
		case "bash":
			cmd = exec.CommandContext(ctx, "/bin/bash", "-s")
		case "python":
			cmd = exec.CommandContext(ctx, "python3", "-")
		default:
			cmd = exec.CommandContext(ctx, "/bin/sh", "-s")
		}
		cmd.Stdin = strings.NewReader(cfg.Script)
	default:
		return nil, fmt.Errorf("no command or script provided")
	}

	// 进程组：便于超时清理子进程
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}

	if cfg.WorkingDir != "" {
		cmd.Dir = cfg.WorkingDir
	}
	env := os.Environ()
	for k, v := range cfg.Environment {
		env = append(env, k+"="+v)
	}
	cmd.Env = env

	if cfg.RunUser != "" && cfg.RunUser != "root" {
		u, err := user.Lookup(cfg.RunUser)
		if err != nil {
			return nil, fmt.Errorf("lookup run user %q: %w", cfg.RunUser, err)
		}
		uid, _ := strconv.Atoi(u.Uid)
		gid, _ := strconv.Atoi(u.Gid)
		if os.Geteuid() == 0 {
			cmd.SysProcAttr.Credential = &syscall.Credential{Uid: uint32(uid), Gid: uint32(gid)}
		} else if os.Geteuid() != uid {
			return nil, fmt.Errorf("run user %q requires root", cfg.RunUser)
		}
	}
	return cmd, nil
}

// killProcessGroup 终止进程组（先 SIGTERM 后 SIGKILL）
func killProcessGroup(cmd *exec.Cmd) {
	if cmd.Process == nil {
		return
	}
	pgid := cmd.Process.Pid
	// 先 SIGTERM
	syscall.Kill(-pgid, syscall.SIGTERM)
	time.Sleep(2 * time.Second)
	syscall.Kill(-pgid, syscall.SIGKILL)
}

// limitedBuffer 限制大小的缓冲区
type limitedBuffer struct {
	buf        bytes.Buffer
	limit      int64
	totalLimit int64
	total      *int64 // 两个流共享的总量计数
	mu         sync.Mutex
	truncated  bool
}

func (b *limitedBuffer) Write(p []byte) (int, error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	// 单流上限
	if int64(b.buf.Len()) >= b.limit {
		b.truncated = true
		return len(p), nil
	}
	// 总上限
	if b.total != nil && b.totalLimit > 0 && atomic.LoadInt64(b.total) >= b.totalLimit {
		b.truncated = true
		return len(p), nil
	}
	avail := b.limit - int64(b.buf.Len())
	writeLen := int64(len(p))
	if writeLen > avail {
		writeLen = avail
	}
	if b.total != nil && b.totalLimit > 0 && atomic.LoadInt64(b.total)+writeLen > b.totalLimit {
		writeLen = b.totalLimit - atomic.LoadInt64(b.total)
		if writeLen < 0 {
			writeLen = 0
		}
	}
	if writeLen > 0 {
		b.buf.Write(p[:writeLen])
		if b.total != nil {
			atomic.AddInt64(b.total, writeLen)
		}
	}
	if writeLen < int64(len(p)) {
		b.truncated = true
	}
	return len(p), nil
}

func (b *limitedBuffer) String() string { return b.buf.String() }

// streamWriter 将进程输出写入 limitedBuffer 并转发实时分片回调。
// 作为 cmd.Stdout/cmd.Stderr 传入，os/exec 内部通过 pipe+goroutine 拷贝，
// 并在 Wait() 中等待拷贝完成，避免 fd 关闭竞态导致输出丢失。
// 注意：回调中的 chunk 为 os/exec 内部复用 buffer 的切片，调用方需在返回前拷贝或同步消费。
type streamWriter struct {
	buf     *limitedBuffer
	stream  string
	onChunk func(stream string, chunk []byte)
}

// logChunkSize 单次实时日志分片上限。超过该值的块按此切分，
// 避免单帧超 WebSocket 消息限制导致连接被网关断开。
const logChunkSize = 64 << 10

func (w *streamWriter) Write(p []byte) (int, error) {
	orig := len(p)
	w.buf.Write(p)
	if w.onChunk != nil && len(p) > 0 {
		// 按固定大小切分后分发，保证每帧远小于网关消息上限
		for len(p) > 0 {
			n := len(p)
			if n > logChunkSize {
				n = logChunkSize
			}
			// 拷贝一份：p 为进程输出缓冲，后续可能被复用，且异步发送前必须独立
			chunk := make([]byte, n)
			copy(chunk, p[:n])
			w.onChunk(w.stream, chunk)
			p = p[n:]
		}
	}
	return orig, nil
}
