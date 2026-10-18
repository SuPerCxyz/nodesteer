package api

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/SuPerCxyz/nodesteer/internal/models"
)

// TestScriptParameterTypeValidated 参数 type 必须落在白名单内：
// 此前 PUT/POST 接受任意 type（type=not-a-type 返回 200）
// （缺陷 OBS-20261017-SCRIPT-PUT-TYPE-UNVALIDATED）。
func TestScriptParameterTypeValidated(t *testing.T) {
	c := newContractServer(t)

	// POST 非法类型 → 400，不落库
	rec := c.do(t, http.MethodPost, "/api/scripts", `{"name":"bad-param-script","content":"true","parameters":[{"name":"p","type":"not-a-type"}]}`)
	if rec.Code != http.StatusBadRequest || !strings.Contains(rec.Body.String(), "invalid parameter type") {
		t.Fatalf("POST invalid parameter type = %d %s, want 400 invalid parameter type", rec.Code, rec.Body.String())
	}
	list := decodeResource[[]*models.Script](t, c.do(t, http.MethodGet, "/api/scripts", ""))
	for _, s := range list {
		if s.Name == "bad-param-script" {
			t.Fatal("rejected script persisted")
		}
	}

	// 合法类型（含空类型归一为 string）→ 200
	rec = c.do(t, http.MethodPost, "/api/scripts", `{"name":"param-script","content":"true","parameters":[{"name":"a","type":"string"},{"name":"b","type":"number"},{"name":"c","type":"bool"},{"name":"d","type":"secret"},{"name":"e"}]}`)
	if rec.Code != http.StatusOK {
		t.Fatalf("POST valid parameter types = %d %s", rec.Code, rec.Body.String())
	}
	created := decodeResource[models.Script](t, rec)
	if got := created.Parameters[4].Type; got != "string" {
		t.Fatalf("empty parameter type = %q, want normalized string", got)
	}

	// PUT 非法类型 → 400（缺陷本体），且脚本定义不被改写
	rec = c.do(t, http.MethodPut, "/api/scripts/"+created.ID, `{"name":"param-script","content":"true","parameters":[{"name":"a","type":"not-a-type"}]}`)
	if rec.Code != http.StatusBadRequest || !strings.Contains(rec.Body.String(), "invalid parameter type") {
		t.Fatalf("PUT invalid parameter type = %d %s, want 400", rec.Code, rec.Body.String())
	}
	stored := decodeResource[models.Script](t, c.do(t, http.MethodGet, "/api/scripts/"+created.ID, ""))
	if len(stored.Parameters) != 5 || stored.Revision != created.Revision {
		t.Fatalf("rejected PUT changed the script: %+v", stored)
	}

	// PUT 合法类型 → 200
	rec = c.do(t, http.MethodPut, "/api/scripts/"+created.ID, `{"name":"param-script","content":"true","parameters":[{"name":"a","type":"secret"}]}`)
	if rec.Code != http.StatusOK {
		t.Fatalf("PUT valid parameter type = %d %s", rec.Code, rec.Body.String())
	}
	if got := decodeResource[models.Script](t, rec).Parameters[0].Type; got != "secret" {
		t.Fatalf("updated parameter type = %q, want secret", got)
	}
}

// TestUserWhitespaceUsernameRejected 纯空白用户名不得创建或登录；
// 首尾空白会被归一化（缺陷 OBS-20261016-G1-USER-INPUT-NOT-TRIMMED）。
func TestUserWhitespaceUsernameRejected(t *testing.T) {
	c := newContractServer(t)

	rec := c.do(t, http.MethodPost, "/api/users", `{"username":"   ","password":"valid-password","role":"viewer"}`)
	if rec.Code != http.StatusBadRequest {
		t.Fatalf("whitespace-only username = %d %s, want 400", rec.Code, rec.Body.String())
	}
	users := decodeResource[[]*models.User](t, c.do(t, http.MethodGet, "/api/users", ""))
	for _, u := range users {
		if strings.TrimSpace(u.Username) == "" {
			t.Fatalf("blank username persisted: %+v", u)
		}
	}

	// 首尾空白归一化后创建，落库与返回都是归一化值
	rec = c.do(t, http.MethodPost, "/api/users", `{"username":"  padded-user  ","password":"valid-password","role":"viewer"}`)
	if rec.Code != http.StatusOK {
		t.Fatalf("padded username = %d %s", rec.Code, rec.Body.String())
	}
	created := decodeResource[models.User](t, rec)
	if created.Username != "padded-user" {
		t.Fatalf("username = %q, want trimmed padded-user", created.Username)
	}
	if token := loginAs(t, c.server, "padded-user", "valid-password"); token == "" {
		t.Fatal("trimmed username must be able to log in")
	}

	// 纯空白用户名登录一律 401（历史遗留的空壳账号也无法登录）
	loginReq := httptest.NewRequest(http.MethodPost, "/api/login", strings.NewReader(`{"username":"   ","password":"valid-password"}`))
	loginReq.Header.Set("Content-Type", "application/json")
	loginRec := httptest.NewRecorder()
	c.handler.ServeHTTP(loginRec, loginReq)
	if loginRec.Code != http.StatusUnauthorized {
		t.Fatalf("whitespace username login = %d %s, want 401", loginRec.Code, loginRec.Body.String())
	}
}
