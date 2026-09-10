package main

import "testing"

func TestDefaultConfigRequiresExplicitAdminPassword(t *testing.T) {
	cfg := DefaultConfig()
	if cfg.AdminPassword != "" {
		t.Fatal("default admin password must be empty")
	}
	if validateAdminCredentials(cfg) == nil {
		t.Fatal("empty admin credentials must be rejected")
	}
	cfg.AdminUsername = "configured-admin"
	cfg.AdminPassword = "configured-password"
	if err := validateAdminCredentials(cfg); err != nil {
		t.Fatalf("configured credentials rejected: %v", err)
	}
}
