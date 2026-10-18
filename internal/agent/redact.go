package agent

import (
	"encoding/binary"
	"slices"
	"strings"
)

// redactedMarker 是 Secret 及其分片在日志中的替换占位符。
const redactedMarker = "[REDACTED]"

// minFragmentLen 是分片遮蔽阈值：Secret 中任意长度达到该值的连续片段都会被遮蔽。
//
// 取 8 的依据：X16（E2E-20261017-SECRET-SPLIT-VALUE-RECONSTRUCT）复现的三个拆片形态
// （同一分片去头/去尾各 8 字符、跨 stdout/stderr 各一段、大输出头尾各一段）产生的
// 片段长度都 >= 19，阈值 8 覆盖这些形态并留有余量；更短的片段与普通文本无法区分，
// 不做遮蔽（残余形态见修复报告）。
// 该值同时决定分片打包方式（8 字节 == uint64），调整时必须同步 packGram/packGramFill。
const minFragmentLen = 8

// 编译期约束：packGram 按 8 字节打包分片，minFragmentLen 不是 8 时编译失败。
const _ = uint(minFragmentLen - 8)
const _ = uint(8 - minFragmentLen)

// maxRunHold 是单个遮蔽区间最多保留的字节数：达到上限立即写出占位符并继续，
// 使流式脱敏的内存占用不随日志长度增长（单流上界 ≈ maxRunHold + 单次分片）。
const maxRunHold = 64 << 10

// secretIndex 是一次执行尝试内全部 Secret 的只读分片索引，stdout/stderr 两个流共享。
//
// 内存上界：分片索引 8 字节/条目，约为 Secret 总字节数的 8 倍，与日志长度无关；
// 不对 Secret 长度设人为上限（Secret 本身已随执行负载常驻内存）。
type secretIndex struct {
	secrets []string // 非空 Secret，长度降序（长值优先）
	short   []string // 长度 < minFragmentLen 的 Secret，按整值匹配
	grams   []uint64 // 长度 >= minFragmentLen 的 Secret 的全部 minFragmentLen 长分片（升序去重）
	// ends 是"末尾两字节是否可能结束某个分片"的预筛位图（64Kbit），
	// 让普通输出上的分片查找退化为一次数组访问。
	ends [1 << 16 / 8]byte
}

func newSecretIndex(secrets []string) *secretIndex {
	idx := &secretIndex{}
	for _, s := range secrets {
		if s != "" {
			idx.secrets = append(idx.secrets, s)
		}
	}
	slices.SortFunc(idx.secrets, func(a, b string) int { return len(b) - len(a) })
	for _, s := range idx.secrets {
		if len(s) < minFragmentLen {
			idx.short = append(idx.short, s)
			continue
		}
		for i := 0; i+minFragmentLen <= len(s); i++ {
			idx.grams = append(idx.grams, packGram(s[i:i+minFragmentLen]))
		}
	}
	slices.Sort(idx.grams)
	idx.grams = slices.Compact(idx.grams)
	for _, g := range idx.grams {
		key := uint16(g & 0xffff) // 大端打包的低 16 位即分片末尾两字节
		idx.ends[key>>3] |= 1 << (key & 7)
	}
	return idx
}

// mayEndGram 报告末尾两字节 b1b2 是否可能结束某个分片（必要条件，不命中即一定不是分片结尾）。
func (idx *secretIndex) mayEndGram(b1, b2 byte) bool {
	key := uint16(b1)<<8 | uint16(b2)
	return idx.ends[key>>3]&(1<<(key&7)) != 0
}

// packGram 把 minFragmentLen（8）字节分片打包为 uint64。
// 使用大端序：打包值的自然顺序与字节字典序一致，可直接做前缀范围查询。
func packGram(s string) uint64 {
	var buf [8]byte
	copy(buf[:], s)
	return binary.BigEndian.Uint64(buf[:])
}

// packGramFill 把不足 minFragmentLen 字节的 s 用 fill 补齐后打包，
// 用于构造"以 s 为前缀的分片"的取值范围 [s+0x00..., s+0xff...]。
func packGramFill(s string, fill byte) uint64 {
	var buf [8]byte
	copy(buf[:], s)
	for i := len(s); i < minFragmentLen; i++ {
		buf[i] = fill
	}
	return binary.BigEndian.Uint64(buf[:])
}

// empty 报告索引中是否没有可用 Secret（无 Secret 时走零开销直通路径）。
func (idx *secretIndex) empty() bool {
	return idx == nil || len(idx.secrets) == 0
}

// matchTail 判定 hold 的前 n 字节是否以危险片段结尾，返回片段起点（可能为负，表示起点落在
// look 回看缓冲中，调用方按 0 处理）。look 是已被遮蔽、不再输出的尾部字节，只用于窗口续接判定：
// 遮蔽区间结束于某个片段内部时，后续字节仍属于该片段，必须能被识别。
// 长度 >= minFragmentLen 的 Secret 用分片索引匹配，更短的 Secret 用整值比较。
func (idx *secretIndex) matchTail(look, hold string, n int) (int, bool) {
	switch {
	case n >= minFragmentLen:
		if idx.mayEndGram(hold[n-2], hold[n-1]) {
			if _, ok := slices.BinarySearch(idx.grams, packGram(hold[n-minFragmentLen:n])); ok {
				return n - minFragmentLen, true
			}
		}
	case len(look) >= minFragmentLen-n:
		window := look[len(look)-(minFragmentLen-n):] + hold[:n]
		if idx.mayEndGram(window[len(window)-2], window[len(window)-1]) {
			if _, ok := slices.BinarySearch(idx.grams, packGram(window)); ok {
				return n - minFragmentLen, true
			}
		}
	}
	for _, s := range idx.short {
		if n >= len(s) && hold[n-len(s):n] == s {
			return n - len(s), true
		}
	}
	return 0, false
}

// isSecretPrefix 报告 s 是否可能是被截断的 Secret 起始片段。
func (idx *secretIndex) isSecretPrefix(s string) bool {
	if s == "" {
		return false
	}
	for _, secret := range idx.secrets {
		if len(s) < len(secret) && strings.HasPrefix(secret, s) {
			return true
		}
	}
	return false
}

// couldStartFragment 报告 s（长度 < minFragmentLen）是否可能是某个分片或短 Secret 的起始片段。
// 判定是结论性的：返回 false 时，s 追加任意后续字节都不可能构成分片。
func (idx *secretIndex) couldStartFragment(s string) bool {
	if s == "" {
		return false
	}
	for _, short := range idx.short {
		if len(s) < len(short) && strings.HasPrefix(short, s) {
			return true
		}
	}
	if len(s) >= minFragmentLen || len(idx.grams) == 0 {
		return false
	}
	i, _ := slices.BinarySearch(idx.grams, packGramFill(s, 0x00))
	return i < len(idx.grams) && idx.grams[i] <= packGramFill(s, 0xff)
}

// jointTailDanger 报告两个流保留的尾部拼接后是否可能构成完整分片。
// 每侧只取末尾 minFragmentLen-1 字节：跨流片段必然落在该窗口内，判定长度有界。
func (idx *secretIndex) jointTailDanger(a, b string) bool {
	if idx.empty() || a == "" || b == "" {
		return false
	}
	if len(a) > minFragmentLen-1 {
		a = a[len(a)-(minFragmentLen-1):]
	}
	if len(b) > minFragmentLen-1 {
		b = b[len(b)-(minFragmentLen-1):]
	}
	for _, joined := range [2]string{a + b, b + a} {
		for i := 0; i+minFragmentLen <= len(joined); i++ {
			if _, ok := slices.BinarySearch(idx.grams, packGram(joined[i:i+minFragmentLen])); ok {
				return true
			}
		}
		for _, s := range idx.short {
			if strings.Contains(joined, s) {
				return true
			}
		}
	}
	return false
}

// streamRedactor 对流式输出做跨分片的状态化脱敏：
// 只保留"可能是分片起点"的尾部（<= minFragmentLen-1 字节）与已确认的遮蔽区间，
// 命中 Secret 或其分片时遮蔽整个连续片段，普通输出不额外等待。
// 每次执行尝试、每个日志流各持有一个实例，由调用方串行使用（同一实例不得并发调用）。
type streamRedactor struct {
	idx     *secretIndex
	hold    string // 尚未写出的尾部字节（有界）
	look    string // 已遮蔽、不再输出的回看字节（<= minFragmentLen-1，只用于窗口续接判定）
	scanned int    // hold 中已完成片段判定（含跨字节续接）的字节数
	run     int    // hold 中当前遮蔽区间起点；-1 表示无
	safe    int    // hold 中已确认不是分片起点、可安全写出的前缀长度
}

func newStreamRedactor(idx *secretIndex) *streamRedactor {
	return &streamRedactor{idx: idx, run: -1}
}

// write 追加一个分片并返回可立即写出的内容；flush 表示该流已结束。
func (r *streamRedactor) write(chunk string, flush bool) string {
	if r.idx.empty() {
		return chunk // 无 Secret：直通，不缓冲、不扫描
	}
	r.hold += chunk
	var out strings.Builder
	for r.scanned < len(r.hold) {
		n := r.scanned + 1
		if start, hit := r.idx.matchTail(r.look, r.hold, n); hit {
			if start < 0 {
				start = 0 // 片段起点在回看缓冲中：该部分已被遮蔽，只继续遮蔽 hold 侧
			}
			if r.run < 0 || start < r.run {
				r.run = start
			}
			r.scanned = n
			if n-r.run >= maxRunHold {
				// 区间过长（例如超长 Secret 被整体打印）：先写出已确认的遮蔽内容，保持内存有界
				out.WriteString(r.hold[:r.run])
				out.WriteString(redactedMarker)
				r.keepLook(r.hold[r.run:n])
				r.hold = r.hold[n:]
				r.scanned, r.run, r.safe = 0, -1, 0
			}
			continue
		}
		if r.run >= 0 {
			// 区间结束：写出区间前的普通字节与占位符，当前字节保留重新判定；
			// 区间尾部进回看缓冲，供"片段起点被本次遮蔽吃掉、后续字节仍属该片段"的续接判定
			out.WriteString(r.hold[:r.run])
			out.WriteString(redactedMarker)
			r.keepLook(r.hold[r.run : n-1])
			r.hold = r.hold[n-1:]
			r.scanned, r.run, r.safe = 0, -1, 0
			continue
		}
		r.scanned = n
	}
	// 推进 safe：遮蔽区间之前的字节已确认不是分片起点；
	// 尾部不足 minFragmentLen 字节时，只保留可能构成分片起点的部分。
	for r.safe < len(r.hold) {
		if r.run >= 0 && r.safe >= r.run {
			break // 遮蔽区间内容不能写出
		}
		if len(r.hold)-r.safe < minFragmentLen && r.idx.couldStartFragment(r.hold[r.safe:]) {
			break // 可能是分片起点：保留等待后续分片续接
		}
		r.safe++
	}
	if flush {
		out.WriteString(r.finish(false))
		return out.String()
	}
	if r.safe > 0 {
		out.WriteString(r.hold[:r.safe])
		r.look = "" // 普通字节已写出，续接关系中断
		r.hold = r.hold[r.safe:]
		r.scanned -= r.safe
		if r.scanned < 0 {
			r.scanned = 0
		}
		if r.run >= 0 {
			r.run -= r.safe
		}
		r.safe = 0
	}
	return out.String()
}

// keepLook 记录已被遮蔽、不再输出的尾部字节，供后续窗口续接判定使用（有界）。
func (r *streamRedactor) keepLook(consumed string) {
	combined := r.look + consumed
	if len(combined) > minFragmentLen-1 {
		combined = combined[len(combined)-(minFragmentLen-1):]
	}
	r.look = combined
}

// pendingTail 返回流结束时尚未决定去留的尾部（遮蔽区间之前的部分）。
func (r *streamRedactor) pendingTail() string {
	return r.hold[min(r.safe, len(r.hold)):]
}

// finish 结束当前流：写出可安全输出的前缀，并按既有口径处理保留的尾部。
// forceRedact 为真（跨流联合判定命中）时尾部整体遮蔽。
func (r *streamRedactor) finish(forceRedact bool) string {
	if r.idx.empty() {
		return ""
	}
	var out strings.Builder
	prefix := r.hold[:min(r.safe, len(r.hold))]
	held := r.hold[len(prefix):]
	out.WriteString(prefix)
	switch {
	case r.run >= 0:
		// 被切断的遮蔽区间：整体遮蔽
		out.WriteString(redactedMarker)
	case len(held) > 0 && (forceRedact || r.idx.isSecretPrefix(held)):
		// 跨流拼接命中，或可能是被切断的 Secret 起始片段：整体遮蔽
		out.WriteString(redactedMarker)
	default:
		// 不足阈值的尾部与普通文本无法区分，按普通字节写出
		out.WriteString(held)
	}
	r.hold, r.look, r.scanned, r.run, r.safe = "", "", 0, -1, 0
	return out.String()
}

// finishStreamPair 结束同一执行尝试的 stdout/stderr：
// 先各自写出可安全输出的部分，再对两流保留尾部做联合判定，最后写出尾部。
// 联合判定覆盖"两流各留一段、并集构成完整分片"的跨流拆片形态。
func finishStreamPair(stdout, stderr *streamRedactor) (string, string) {
	out := stdout.write("", false)
	errOut := stderr.write("", false)
	joint := stdout.idx.jointTailDanger(stdout.pendingTail(), stderr.pendingTail())
	return out + stdout.finish(joint), errOut + stderr.finish(joint)
}

// redact 对完整文本做脱敏（终态字段），与实时分片使用同一套判定。
func (idx *secretIndex) redact(value string) string {
	return newStreamRedactor(idx).write(value, true)
}

// redactStreams 对同一执行尝试的 stdout/stderr 完整文本做脱敏。
func redactStreams(idx *secretIndex, stdout, stderr string) (string, string) {
	outR, errR := newStreamRedactor(idx), newStreamRedactor(idx)
	outHead := outR.write(stdout, false)
	errHead := errR.write(stderr, false)
	outTail, errTail := finishStreamPair(outR, errR)
	return outHead + outTail, errHead + errTail
}

// redact 对完整文本做脱敏（单值场景）。
func redact(value string, secrets []string) string {
	return newSecretIndex(secrets).redact(value)
}
