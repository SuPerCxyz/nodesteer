package hubserver

import (
	"bytes"
	"compress/gzip"
	"crypto/sha256"
	"encoding/hex"
	"io/fs"
	"mime"
	"net/http"
	"path"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"time"
)

const (
	staticCacheLimit    = 512
	staticGzipMinBytes  = 1024
	staticHTMLMaxAge    = "no-cache"
	staticImmutableAge  = "public, max-age=31536000, immutable"
	staticDefaultMaxAge = "public, max-age=86400"
)

var contentHashPattern = regexp.MustCompile(`-[0-9a-f]{8,}\.`)

var staticContentTypes = map[string]string{
	".html":  "text/html; charset=utf-8",
	".js":    "text/javascript; charset=utf-8",
	".mjs":   "text/javascript; charset=utf-8",
	".css":   "text/css; charset=utf-8",
	".json":  "application/json; charset=utf-8",
	".map":   "application/json; charset=utf-8",
	".svg":   "image/svg+xml",
	".txt":   "text/plain; charset=utf-8",
	".xml":   "application/xml; charset=utf-8",
	".wasm":  "application/wasm",
	".woff":  "font/woff",
	".woff2": "font/woff2",
	".ttf":   "font/ttf",
	".otf":   "font/otf",
	".png":   "image/png",
	".jpg":   "image/jpeg",
	".jpeg":  "image/jpeg",
	".gif":   "image/gif",
	".webp":  "image/webp",
	".ico":   "image/x-icon",
}

type staticAsset struct {
	data        []byte
	etag        string
	modTime     time.Time
	contentType string
	encoded     string
}

type staticAssetCache struct {
	mu      sync.Mutex
	entries map[string]*staticAsset
}

var staticGzipPool = sync.Pool{
	New: func() any {
		writer, _ := gzip.NewWriterLevel(nil, gzip.BestSpeed)
		return writer
	},
}

func newStaticHandler(fsys fs.FS) http.Handler {
	cache := &staticAssetCache{entries: map[string]*staticAsset{}}
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet && r.Method != http.MethodHead {
			w.Header().Set("Allow", "GET, HEAD")
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
			return
		}
		name := strings.TrimPrefix(r.URL.Path, "/")
		if name == "" {
			name = "index.html"
		}
		name = path.Clean(name)
		if name == "." || strings.HasPrefix(name, "../") || strings.HasPrefix(name, "/") || !fs.ValidPath(name) {
			http.NotFound(w, r)
			return
		}
		if info, err := fs.Stat(fsys, name); err == nil && info.IsDir() {
			name = path.Join(name, "index.html")
		}
		info, err := fs.Stat(fsys, name)
		if err != nil {
			// SPA 回退：无扩展名的前端路由交给 index.html。
			if strings.Contains(path.Base(name), ".") {
				http.NotFound(w, r)
				return
			}
			name = "index.html"
			info, err = fs.Stat(fsys, name)
			if err != nil {
				http.NotFound(w, r)
				return
			}
		}

		cacheControl := staticCacheControl(name)
		contentType := staticContentType(name)
		compressible := staticCompressible(name)
		acceptsBrotli := acceptsEncoding(r, "br")
		acceptsGzip := acceptsEncoding(r, "gzip")
		encoded := ""
		if compressible {
			addVary(w.Header(), "Accept-Encoding")
			if acceptsBrotli {
				if _, err := fs.Stat(fsys, name+".br"); err == nil {
					encoded = "br"
				}
			}
			if encoded == "" && acceptsGzip {
				encoded = "gzip"
			}
		}
		cacheKey := name + "|" + encoded
		asset, ok := cache.get(cacheKey)
		if !ok {
			data, err := fs.ReadFile(fsys, name)
			if err != nil {
				http.NotFound(w, r)
				return
			}
			asset = &staticAsset{data: data, modTime: info.ModTime(), contentType: contentType}
			switch encoded {
			case "br":
				compressed, err := fs.ReadFile(fsys, name+".br")
				if err != nil {
					encoded = ""
					cacheKey = name
				} else {
					asset.data = compressed
				}
			case "gzip":
				if len(data) >= staticGzipMinBytes {
					var buffer bytes.Buffer
					gz := staticGzipPool.Get().(*gzip.Writer)
					gz.Reset(&buffer)
					_, writeErr := gz.Write(data)
					closeErr := gz.Close()
					staticGzipPool.Put(gz)
					if writeErr == nil && closeErr == nil {
						asset.data = buffer.Bytes()
					} else {
						encoded = ""
						cacheKey = name
					}
				} else {
					encoded = ""
					cacheKey = name
				}
			}
			asset.encoded = encoded
			asset.etag = staticETag(asset.data)
			cache.put(cacheKey, asset)
		}

		etag := asset.etag
		w.Header().Set("ETag", etag)
		w.Header().Set("Cache-Control", cacheControl)
		w.Header().Set("Content-Type", asset.contentType)
		if !asset.modTime.IsZero() {
			w.Header().Set("Last-Modified", asset.modTime.UTC().Format(http.TimeFormat))
		}
		if asset.encoded != "" {
			w.Header().Set("Content-Encoding", asset.encoded)
		}
		if staticNotModified(r, etag, asset.modTime) {
			w.WriteHeader(http.StatusNotModified)
			return
		}
		w.Header().Set("Content-Length", strconv.Itoa(len(asset.data)))
		if r.Method == http.MethodHead {
			w.WriteHeader(http.StatusOK)
			return
		}
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write(asset.data)
	})
}

func (c *staticAssetCache) get(key string) (*staticAsset, bool) {
	c.mu.Lock()
	defer c.mu.Unlock()
	asset, ok := c.entries[key]
	return asset, ok
}

func (c *staticAssetCache) put(key string, asset *staticAsset) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if len(c.entries) >= staticCacheLimit {
		c.entries = map[string]*staticAsset{}
	}
	c.entries[key] = asset
}

func staticETag(data []byte) string {
	sum := sha256.Sum256(data)
	return `"` + hex.EncodeToString(sum[:12]) + `"`
}

func staticNotModified(r *http.Request, etag string, modTime time.Time) bool {
	if match := r.Header.Get("If-None-Match"); match != "" {
		for _, item := range strings.Split(match, ",") {
			if strings.TrimSpace(item) == etag {
				return true
			}
		}
	}
	if !modTime.IsZero() {
		if since := r.Header.Get("If-Modified-Since"); since != "" {
			if parsed, err := http.ParseTime(since); err == nil && !modTime.After(parsed) {
				return true
			}
		}
	}
	return false
}

func staticCacheControl(name string) string {
	if strings.HasSuffix(name, ".html") {
		return staticHTMLMaxAge
	}
	if strings.HasPrefix(name, "assets/") || contentHashPattern.MatchString(path.Base(name)) {
		return staticImmutableAge
	}
	return staticDefaultMaxAge
}

func staticContentType(name string) string {
	if contentType, ok := staticContentTypes[strings.ToLower(path.Ext(name))]; ok {
		return contentType
	}
	if contentType := mime.TypeByExtension(path.Ext(name)); contentType != "" {
		return contentType
	}
	return "application/octet-stream"
}

func staticCompressible(name string) bool {
	switch strings.ToLower(path.Ext(name)) {
	case ".html", ".js", ".mjs", ".css", ".json", ".map", ".svg", ".txt", ".xml", ".wasm":
		return true
	default:
		return false
	}
}

func acceptsEncoding(r *http.Request, encoding string) bool {
	for _, part := range strings.Split(r.Header.Get("Accept-Encoding"), ",") {
		fields := strings.Split(part, ";")
		token := strings.TrimSpace(fields[0])
		if !strings.EqualFold(token, encoding) && token != "*" {
			continue
		}
		rejected := false
		for _, param := range fields[1:] {
			keyValue := strings.SplitN(strings.TrimSpace(param), "=", 2)
			if len(keyValue) == 2 && strings.EqualFold(strings.TrimSpace(keyValue[0]), "q") &&
				strings.TrimSpace(keyValue[1]) == "0" {
				rejected = true
			}
		}
		if !rejected {
			return true
		}
	}
	return false
}

func addVary(header http.Header, value string) {
	for _, existing := range header.Values("Vary") {
		for _, item := range strings.Split(existing, ",") {
			if strings.EqualFold(strings.TrimSpace(item), value) {
				return
			}
		}
	}
	header.Add("Vary", value)
}
