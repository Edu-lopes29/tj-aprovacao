# Servidor local com proxy inteligente para Planalto.gov.br
# Usa BeautifulSoup para extrair só o corpo da lei (sem menus, rodapés, JS)
# Uso: python servidor.py
# Acesse: http://localhost:9000/tj-planner.html

import http.server, urllib.parse, sys, os, json
from pathlib import Path

PORT = 9000
BASE_DIR = Path(__file__).parent

import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.5",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
}

ALLOWED_DOMAINS = ["planalto.gov.br", "senado.leg.br", "camara.leg.br"]


def fetch_lei_html(url: str) -> str:
    """Baixa a lei do Planalto e retorna HTML formatado só com o texto da lei."""
    # Timeout maior para leis grandes (CC tem 922 KB)
    resp = requests.get(url, headers=HEADERS, timeout=60, allow_redirects=True)
    resp.raise_for_status()

    # Detecta encoding pelo header HTTP primeiro, depois pelo conteúdo
    raw = resp.content
    ct = resp.headers.get("Content-Type", "")
    import re as _re
    m = _re.search(r"charset=([^\s;\"']+)", ct, _re.I)
    enc = m.group(1) if m else None
    if not enc:
        # Tenta detectar no meta tag do HTML
        sniff = raw[:4096].decode("latin-1", errors="replace")
        m2 = _re.search(r'charset=["\']?([^\s;"\'/>]+)', sniff, _re.I)
        enc = m2.group(1) if m2 else "windows-1252"
    try:
        html = raw.decode(enc, errors="replace")
    except (LookupError, UnicodeDecodeError):
        html = raw.decode("windows-1252", errors="replace")

    # usa html.parser (mais tolerante com HTML antigo do Planalto/FrontPage)
    soup = BeautifulSoup(html, "html.parser")

    # Remove elementos indesejados
    for tag in soup.find_all(["script", "style", "nav", "header", "footer",
                               "noscript", "iframe", "form", "button",
                               "aside", "link", "meta", "head"]):
        tag.decompose()

    # Extrai todos os <p> e reconstrói o conteúdo
    # (necessário para HTML do FrontPage onde <p> ficam fora do <body> no parse tree)
    all_ps = soup.find_all(["p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "table"])

    if not all_ps:
        # Fallback: tenta corpo inteiro
        body = soup.find("body") or soup
        resultado = f'<div style="font-family:inherit;line-height:1.8;color:inherit">{body}</div>'
        return resultado

    SAFE_TAGS = {"p", "br", "b", "strong", "em", "i", "u", "h1", "h2", "h3", "h4",
                 "h5", "h6", "span", "a", "ul", "ol", "li", "blockquote",
                 "mark", "sub", "sup", "hr", "table", "tr", "td", "th",
                 "thead", "tbody", "font", "center", "div"}

    partes = []
    seen_ids = set()
    for tag in all_ps:
        # Evita duplicatas por id
        tid = tag.get("id")
        if tid:
            if tid in seen_ids:
                continue
            seen_ids.add(tid)

        # Limpa atributos
        for child in tag.find_all(True):
            if child.name not in SAFE_TAGS:
                child.unwrap()
                continue
            attrs_to_keep = {}
            if child.name == "a" and child.get("href"):
                attrs_to_keep["href"] = child["href"]
                attrs_to_keep["target"] = "_blank"
            if child.get("id"):
                attrs_to_keep["id"] = child["id"]
            if child.name in ("p", "td", "th") and child.get("align"):
                attrs_to_keep["align"] = child["align"]
            child.attrs = attrs_to_keep

        # Limpa atributos do próprio tag
        attrs_to_keep = {}
        if tag.get("id"):
            attrs_to_keep["id"] = tag["id"]
        if tag.name in ("p", "td", "th") and tag.get("align"):
            attrs_to_keep["align"] = tag["align"]
        tag.attrs = attrs_to_keep

        partes.append(str(tag))

    resultado = '<div style="font-family:inherit;line-height:1.8;color:inherit">' + "\n".join(partes) + "</div>"
    return resultado


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(BASE_DIR), **kwargs)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)

        if parsed.path == "/proxy":
            params = urllib.parse.parse_qs(parsed.query)
            url = params.get("url", [None])[0]

            if not url:
                self._json_error(400, "Parâmetro 'url' ausente")
                return

            if not any(d in url for d in ALLOWED_DOMAINS):
                self._json_error(403, "Domínio não permitido")
                return

            try:
                print(f"[proxy] Buscando: {url}")
                content = fetch_lei_html(url)
                body = content.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Cache-Control", "public, max-age=3600")
                self.end_headers()
                self.wfile.write(body)
                print(f"[proxy] OK — {len(body)//1024} KB")
            except Exception as e:
                print(f"[proxy] ERRO: {e}")
                msg = f"<p style='color:#EF5350'>Erro ao buscar a lei: {e}</p>"
                body = msg.encode("utf-8")
                self.send_response(502)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(body)
            return

        # Arquivos estáticos
        super().do_GET()

    def _json_error(self, code, msg):
        body = json.dumps({"erro": msg}).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        if "proxy" in str(args):
            print(f"  {args}")
        # Silencia logs de arquivos estáticos


if __name__ == "__main__":
    os.chdir(BASE_DIR)
    print(f"=" * 50)
    print(f"Servidor TJ Aprovação")
    print(f"http://localhost:{PORT}/tj-planner.html")
    print(f"Proxy Planalto ativo")
    print(f"=" * 50)
    with http.server.HTTPServer(("127.0.0.1", PORT), Handler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nServidor encerrado.")
