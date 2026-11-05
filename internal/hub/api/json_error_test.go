package api

import (
	"math"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestWriteJSONRejectsNonFiniteWithoutEmptySuccess(t *testing.T) {
	recorder := httptest.NewRecorder()
	writeJSON(recorder, http.StatusOK, map[string]float64{"value": math.Inf(1)})
	if recorder.Code != http.StatusInternalServerError {
		t.Fatalf("status = %d, want 500", recorder.Code)
	}
	if recorder.Body.Len() == 0 {
		t.Fatal("non-finite response produced empty body")
	}
}
