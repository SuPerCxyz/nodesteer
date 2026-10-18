package agent

import (
	"strings"
	"testing"

	"github.com/SuPerCxyz/nodesteer/internal/protocol"
)

// TestBuildUnitEnvironmentFileUnquoted 断言生成的 systemd Unit 中
// EnvironmentFile= 的值是**未加引号**的 "-<绝对路径>"。
//
// 实测 systemd 259（EnvironmentFile= 不参与词拆分，且前导 "-" 只按值首字符识别）：
//   - EnvironmentFile="-"/etc/x.conf → "path is not absolute, ignoring"，
//     文件被静默忽略 → 应用「配置内容」运行期完全不生效；
//   - EnvironmentFile=-/etc/x.conf → 正常加载（变量生效）。
//
// 缺陷 APPUI-20261016-ENVFILE-QUOTED-DASH。
func TestBuildUnitEnvironmentFileUnquoted(t *testing.T) {
	am := &ApplicationManager{}
	cases := []struct {
		name string
		path string
		want string
	}{
		{name: "普通路径", path: "/etc/nodesteer-app.conf", want: "EnvironmentFile=-/etc/nodesteer-app.conf"},
		// 该指令不做词拆分：含空格路径同样是一个完整值，加引号反而会失效。
		{name: "含空格路径", path: "/etc/nodesteer app.conf", want: "EnvironmentFile=-/etc/nodesteer app.conf"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			unit := am.buildUnit("nodesteer-app.service", protocol.DeployRequestPayload{
				AppID:       "app-1",
				ConfigPath:  tc.path,
				Environment: map[string]string{"K": "v"},
			}, "/usr/local/bin/app")

			if !strings.Contains(unit, tc.want+"\n") {
				t.Fatalf("unit missing unquoted EnvironmentFile %q:\n%s", tc.want, unit)
			}
			for _, line := range strings.Split(unit, "\n") {
				if strings.HasPrefix(line, "EnvironmentFile=") && strings.Contains(line, `"`) {
					t.Fatalf("EnvironmentFile must not be quoted: %q", line)
				}
			}
			// ExecStart 仍按 systemd 词拆分规则加引号（含空格的二进制路径）。
			if !strings.Contains(unit, "ExecStart=\"/usr/local/bin/app\"") {
				t.Fatalf("ExecStart quoting changed unexpectedly:\n%s", unit)
			}
		})
	}
}

// TestBuildUnitOmitsEnvironmentFileWithoutConfigPath 无配置路径时不生成 EnvironmentFile 行。
func TestBuildUnitOmitsEnvironmentFileWithoutConfigPath(t *testing.T) {
	am := &ApplicationManager{}
	unit := am.buildUnit("nodesteer-app.service", protocol.DeployRequestPayload{AppID: "app-1"}, "/usr/local/bin/app")
	if strings.Contains(unit, "EnvironmentFile=") {
		t.Fatalf("unexpected EnvironmentFile without config path:\n%s", unit)
	}
}

// TestValidateUnitEmbeddedPathRejectsControlCharacters Unit 内嵌路径不得含控制字符
// （换行会注入新的 Unit 指令行）。
func TestValidateUnitEmbeddedPathRejectsControlCharacters(t *testing.T) {
	for _, ok := range []string{"/etc/nodesteer-app.conf", "/etc/nodesteer app.conf", "/opt/应用/配置.conf"} {
		if err := validateUnitEmbeddedPath(ok); err != nil {
			t.Fatalf("path %q rejected: %v", ok, err)
		}
	}
	for _, bad := range []string{"/etc/app\nEnvironment=INJECTED=1", "/etc/app\r.conf", "/etc/app\t.conf", "/etc/app\x00.conf"} {
		if err := validateUnitEmbeddedPath(bad); err == nil {
			t.Fatalf("path %q accepted, want rejection", bad)
		}
	}
}

// TestDeployRejectsConfigPathWithControlCharacters 带控制字符的配置路径必须在准备阶段
// 就被拒绝：不产生任何宿主副作用（不备份、不停服务、不写文件）。
func TestDeployRejectsConfigPathWithControlCharacters(t *testing.T) {
	ctx := t.Context()
	am, h, app := recoveryFixture(t)
	var result protocol.DeployResultPayload
	am.deploy(ctx, protocol.DeployRequestPayload{
		AppID:      app.ID,
		AppVersion: "v2",
		BinaryPath: app.BinaryPath,
		ConfigPath: "/etc/app\nEnvironment=INJECTED=1",
		UnitName:   app.UnitName,
		Config:     "new config",
		Operation:  "deploy",
	}, []byte(`{"id":"`+app.ID+`","name":"app","revision":1,"binary_path":"`+app.BinaryPath+`","unit_name":"`+app.UnitName+`"}`), "exec",
		func(p protocol.DeployResultPayload) { result = p })

	if result.OK || !strings.Contains(result.Error, "control character") {
		t.Fatalf("expected control-character rejection, got %+v", result)
	}
	// 准备阶段拒绝：旧文件与旧服务状态不变，journal 收敛为 prepare_failed。
	if got, err := h.ReadFile(ctx, app.BinaryPath); err != nil || string(got) != "old binary" {
		t.Fatalf("binary changed on rejected deploy: %q %v", got, err)
	}
	if h.active {
		t.Fatal("service must not be started on rejected deploy")
	}
	active, err := am.store.GetActiveDeployments(ctx)
	if err != nil || len(active) != 0 {
		t.Fatalf("rejected deploy left active journal rows: %+v err=%v", active, err)
	}
	rows, err := am.store.ListDeploymentsByApplication(ctx, app.ID)
	if err != nil || len(rows) != 1 || rows[0].CompletedKind != CompletedKindPrepareFailed {
		t.Fatalf("journal completed kind = %+v err=%v, want %s", rows, err, CompletedKindPrepareFailed)
	}
}
