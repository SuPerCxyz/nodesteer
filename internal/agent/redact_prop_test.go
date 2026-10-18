package agent

import (
	"math/rand"
	"strings"
	"testing"
)

// TestStreamRedactorRepetitiveSecretResidual 记录已如实披露的残余形态：
// 当 Secret 字母表极小、且两个分片相邻拼接时，遮蔽区间可能在第二个分片内部因"跨接缝的
// 8 字节窗口恰好命中"而中断，导致第二个分片尾部被当作普通字节写出。
// 断言口径与报告 §6 一致：泄漏部分必须 < minFragmentLen 字节（等价于任意 >=8 字节连续
// 分片都不会原样出现），且普通填充字节不受影响。
func TestStreamRedactorRepetitiveSecretResidual(t *testing.T) {
	secret := "abbaaaabbb"
	// frag1=secret[2:]、frag2=secret[:8]、frag3=secret[2:] 相邻拼接
	input := "baaaabbb" + "QQ" + "abbaaaab" + "bbaaaabb" + "ZZ"
	for _, size := range []int{1, 2, 3, 7, 8, 16} {
		got := feedChunks(t, newSecretIndex([]string{secret}), input, size)
		assertNoFragment(t, got, secret)
		leaked := strings.NewReplacer(redactedMarker, "", "QQ", "", "ZZ", "").Replace(got)
		if len(leaked) >= minFragmentLen {
			t.Fatalf("chunk=%d 泄漏超过阈值：%q（输出 %q）", size, leaked, got)
		}
		if !strings.Contains(got, "QQ") || !strings.Contains(got, "ZZ") {
			t.Fatalf("chunk=%d 普通填充字节被改动：%q", size, got)
		}
	}
}

// TestStreamRedactorRandomizedFragments 是固定种子的属性测试：
// 随机 Secret + 随机 >=minFragmentLen 片段 + 随机分片大小，
// 断言（1）输出不含任何 >=minFragmentLen 的 Secret 分片；（2）普通字节逐字节保留（无过度遮蔽）。
func TestStreamRedactorRandomizedFragments(t *testing.T) {
	rng := rand.New(rand.NewSource(20261017))
	for iter := 0; iter < 300; iter++ {
		n := minFragmentLen + rng.Intn(40)
		secret := make([]byte, n)
		for i := range secret {
			secret[i] = byte('a' + rng.Intn(26))
		}
		var input, plain strings.Builder
		parts := 1 + rng.Intn(3)
		for p := 0; p < parts; p++ {
			filler := strings.Repeat("Q", 1+rng.Intn(20))
			input.WriteString(filler)
			plain.WriteString(filler)
			start := rng.Intn(n - minFragmentLen + 1)
			end := start + minFragmentLen + rng.Intn(n-start-minFragmentLen+1)
			input.WriteString(string(secret[start:end]))
		}
		tail := strings.Repeat("Z", 1+rng.Intn(20))
		input.WriteString(tail)
		plain.WriteString(tail)

		size := 1 + rng.Intn(20)
		got := feedChunks(t, newSecretIndex([]string{string(secret)}), input.String(), size)
		assertNoFragment(t, got, string(secret))
		if stripped := strings.ReplaceAll(got, redactedMarker, ""); stripped != plain.String() {
			t.Fatalf("iter=%d size=%d 普通字节被改动:\n got=%q\nwant=%q", iter, size, stripped, plain.String())
		}
	}
}
