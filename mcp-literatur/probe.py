#!/usr/bin/env python3
"""Literatür MCP'si için uç nokta yoklaması.

Yalnızca standart kütüphane kullanır. Kendi bilgisayarınızda çalıştırın:

    python3 probe.py --email adresiniz@ornek.com

Çıktı: ekrana özet + probe_report.json. Bu dosyanın içeriğini (özellikle
ekran çıktısını) paylaşın. Betik yalnızca GET isteği atar, giriş yapmaz,
CAPTCHA/engel aşmaya çalışmaz; engel görürse bunu raporlar.
"""
import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

UA = "literatur-mcp-probe/0.1 (akademik arastirma; yoklama)"
TIMEOUT = 25
BLOCK_MARKERS = [
    "just a moment", "cf-chl", "captcha", "recaptcha", "hcaptcha",
    "access denied", "attention required", "bir dakika",
    "robot", "güvenlik doğrulaması",
]


def endpoints(email: str):
    q = urllib.parse.quote("idari sözleşme")
    qe = urllib.parse.quote("administrative contract")
    doi = "10.1038/nature12373"
    return [
        # --- Uluslararası (beklenen: kararlı JSON API) ---
        ("openalex", "OpenAlex arama",
         f"https://api.openalex.org/works?search={qe}&per-page=2&mailto={email}"),
        ("crossref", "Crossref arama",
         f"https://api.crossref.org/works?query={qe}&rows=2&mailto={email}"),
        ("crossref", "Crossref DOI (referanslar dahil)",
         f"https://api.crossref.org/works/{doi}?mailto={email}"),
        ("opencitations", "OpenCitations v2 referanslar",
         f"https://api.opencitations.net/index/v2/references/doi:{doi}"),
        ("unpaywall", "Unpaywall DOI",
         f"https://api.unpaywall.org/v2/{doi}?email={email}"),
        ("semanticscholar", "Semantic Scholar arama",
         f"https://api.semanticscholar.org/graph/v1/paper/search?query={qe}&limit=2&fields=title,year,externalIds"),
        ("europepmc", "Europe PMC arama",
         f"https://www.ebi.ac.uk/europepmc/webservices/rest/search?query={qe}&format=json&pageSize=2"),
        # --- Ulusal (DOĞRULANMAMIŞ; amaç keşif) ---
        ("dergipark", "DergiPark ana sayfa", "https://dergipark.org.tr/"),
        ("dergipark", "DergiPark robots.txt", "https://dergipark.org.tr/robots.txt"),
        ("dergipark", "DergiPark arama sayfası",
         f"https://dergipark.org.tr/tr/search?q={q}&section=articles"),
        ("dergipark", "DergiPark OAI-PMH Identify (varsayım)",
         "https://dergipark.org.tr/api/public/oai/?verb=Identify"),
        ("trdizin", "TRDizin ana sayfa", "https://search.trdizin.gov.tr/"),
        ("trdizin", "TRDizin arama API (varsayım)",
         f"https://search.trdizin.gov.tr/api/defaultSearch/publication/?q={q}&order=relevance-DESC&page=1&limit=2"),
        ("yoktez", "YÖK Tez ana sayfa", "https://tez.yok.gov.tr/UlusalTezMerkezi/"),
        ("yoktez", "YÖK Tez robots.txt", "https://tez.yok.gov.tr/robots.txt"),
    ]


def probe(url: str):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "application/json, application/xml, text/html;q=0.8, */*;q=0.5",
    })
    t0 = time.time()
    out = {"url": url}
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            body = r.read(200_000)
            out.update(status=r.status, content_type=r.headers.get("Content-Type"),
                       final_url=r.geturl(), server=r.headers.get("Server"))
    except urllib.error.HTTPError as e:
        body = e.read(200_000) if hasattr(e, "read") else b""
        out.update(status=e.code, content_type=e.headers.get("Content-Type") if e.headers else None,
                   server=e.headers.get("Server") if e.headers else None)
    except Exception as e:  # ağ/DNS/TLS/zaman aşımı
        out.update(status=None, error=f"{type(e).__name__}: {e}")
        out["ms"] = int((time.time() - t0) * 1000)
        return out
    out["ms"] = int((time.time() - t0) * 1000)
    text = body.decode("utf-8", errors="replace")
    out["bytes_read"] = len(body)
    low = text.lower()
    out["block_markers"] = [m for m in BLOCK_MARKERS if m in low]
    ct = (out.get("content_type") or "").lower()
    out["is_json"] = False
    if "json" in ct or text.lstrip().startswith(("{", "[")):
        try:
            obj = json.loads(text)
            out["is_json"] = True
            out["json_top_keys"] = list(obj.keys())[:15] if isinstance(obj, dict) else f"list[{len(obj)}]"
        except Exception:
            pass
    out["looks_xml"] = text.lstrip().startswith("<?xml") or "<OAI-PMH" in text[:500]
    m = re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S)
    out["html_title"] = m.group(1).strip()[:120] if m else None
    out["snippet"] = re.sub(r"\s+", " ", text)[:300]
    return out


def verdict(r):
    s = r.get("status")
    if s is None:
        return "ULASILAMADI"
    if r.get("block_markers") and s != 200:
        return "ENGEL"
    if s == 200 and r.get("block_markers") and not r.get("is_json") and not r.get("looks_xml"):
        return "SUPHELI(engel olabilir)"
    if s == 200:
        return "ACIK"
    return f"HTTP {s}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--email", required=True, help="Polite pool için iletişim e-postası")
    ap.add_argument("--out", default="probe_report.json")
    a = ap.parse_args()
    results = []
    for src, label, url in endpoints(a.email):
        r = probe(url)
        r.update(source=src, label=label, verdict=verdict(r))
        results.append(r)
        print(f"[{r['verdict']:<24}] {src:<16} {label}  "
              f"(status={r.get('status')}, {r.get('content_type')}, {r.get('ms')}ms)")
        if r.get("error"):
            print(f"    hata: {r['error']}")
        time.sleep(1.0)  # nazik hız
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\nTam rapor: {a.out}")
    print(f"Python: {sys.version.split()[0]}")


if __name__ == "__main__":
    main()
