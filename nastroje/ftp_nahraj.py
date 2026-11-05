# Nahraje složku site/ na FTP (FTPS) – pro hosting s vsftpd, který vyžaduje znovupoužití TLS relace (např. WEDOS).
#   python nastroje/ftp_nahraj.py test  HOST NETRC [URL]     zkušební soubor: nahrát, ověřit na webu, smazat
#   python nastroje/ftp_nahraj.py nahraj HOST NETRC [SLOZKA] [VLAKEN]   nahraje site/ (index.html až nakonec), pokračuje po přerušení
# Přihlášení se bere z netrc souboru (machine HOST login ... password ...), heslo se nikde nevypisuje.
import ftplib, json, netrc, os, ssl, sys, threading, time, urllib.request
from queue import Queue, Empty


class FTPS(ftplib.FTP_TLS):
    """FTP_TLS, které pro datové spojení znovu použije TLS relaci řídicího spojení (vsftpd require_ssl_reuse)"""
    def ntransfercmd(self, cmd, rest=None):
        conn, size = ftplib.FTP.ntransfercmd(self, cmd, rest)
        if self._prot_p:
            conn = self.context.wrap_socket(conn, server_hostname=self.host, session=self.sock.session)
        return conn, size


def connect(host, nrc):
    login, _, password = netrc.netrc(nrc).authenticators(host)
    ctx = ssl.create_default_context(); ctx.maximum_version = ssl.TLSVersion.TLSv1_2   # TLS 1.3 relace znovu použít neumí
    f = FTPS(host, timeout=60, context=ctx); f.login(login, password); f.prot_p()
    return f


def mkdirs(f, path, made):
    parts = [p for p in path.split('/') if p]
    for i in range(1, len(parts) + 1):
        d = '/'.join(parts[:i])
        if d in made: continue
        try: f.mkd('/' + d)
        except ftplib.error_perm: pass          # už existuje
        made.add(d)


def test(host, nrc, url):
    f = connect(host, nrc)
    print('přihlášeno, adresář:', f.pwd(), '| obsah:', f.nlst()[:20])
    body = f'izomapa test {time.time():.0f}'.encode()
    import io
    f.storbinary('STOR test.html', io.BytesIO(body))
    print('nahráno test.html', len(body), 'B')
    if url:
        time.sleep(1)
        try:
            r = urllib.request.urlopen(urllib.request.Request(url.rstrip('/') + '/test.html', headers={'User-Agent': 'izomapa-test'}), timeout=30)
            got = r.read(); print('web:', r.status, r.headers.get('content-type'), 'shoda obsahu:', got == body)
        except Exception as e:
            print('web: CHYBA', e)
    f.delete('test.html'); print('test.html smazán, obsah:', f.nlst()[:20]); f.quit()


def upload(host, nrc, src='site', workers=4):
    state_f = os.path.join('build', 'ftp_stav.json')
    state = json.load(open(state_f)) if os.path.exists(state_f) else {}
    files = []
    for root, _, names in os.walk(src):
        for n in names:
            p = os.path.join(root, n); rel = os.path.relpath(p, src).replace(os.sep, '/')
            st = os.stat(p)
            if state.get(rel) == [st.st_size, int(st.st_mtime)]: continue
            files.append((rel, p, st.st_size, int(st.st_mtime)))
    last = [x for x in files if x[0] == 'index.html']; files = [x for x in files if x[0] != 'index.html']
    files.sort(key=lambda x: x[0])
    total = sum(x[2] for x in files + last); print(f'k nahrání {len(files) + len(last)} souborů, {total / 1e6:.0f} MB', flush=True)
    q = Queue(); [q.put(x) for x in files]
    lock = threading.Lock(); done = [0, 0]; errors = []; t0 = time.time()

    def save():
        with lock: json.dump(state, open(state_f, 'w'))

    def worker():
        f = None; made = set()
        while True:
            try: rel, p, size, mt = q.get_nowait()
            except Empty: break
            for attempt in range(6):
                try:
                    if f is None: f = connect(host, nrc); made = set()
                    d = rel.rsplit('/', 1)[0] if '/' in rel else ''
                    if d: mkdirs(f, d, made)
                    with open(p, 'rb') as fh: f.storbinary('STOR /' + rel, fh, blocksize=65536)
                    with lock:
                        state[rel] = [size, mt]; done[0] += 1; done[1] += size
                        if done[0] % 100 == 0:
                            el = time.time() - t0
                            print(f'{done[0]} souborů, {done[1] / 1e6:.0f}/{total / 1e6:.0f} MB, {done[1] / 1e6 / max(el, 1) * 8:.1f} Mbit/s', flush=True)
                    if done[0] % 100 == 0: save()
                    break
                except Exception as e:
                    try: f and f.close()
                    except Exception: pass
                    f = None; time.sleep(2 * (attempt + 1))
                    if attempt == 5:
                        with lock: errors.append((rel, repr(e)))
        if f:
            try: f.quit()
            except Exception: pass

    th = [threading.Thread(target=worker) for _ in range(workers)]
    [t.start() for t in th]; [t.join() for t in th]; save()
    if errors:
        print('CHYBY', len(errors), errors[:10], flush=True); print('index.html se nenahrál, protože něco selhalo – spusť znovu'); sys.exit(1)
    for rel, p, size, mt in last:                      # index.html až nakonec: web se ukáže až kompletní
        f = connect(host, nrc)
        with open(p, 'rb') as fh: f.storbinary('STOR /' + rel, fh)
        f.quit(); state[rel] = [size, mt]; save()
    print(f'HOTOVO: {done[0] + len(last)} souborů, {done[1] / 1e6:.0f} MB za {(time.time() - t0) / 60:.1f} min', flush=True)


if __name__ == '__main__':
    cmd, host, nrc = sys.argv[1], sys.argv[2], sys.argv[3]
    if cmd == 'test': test(host, nrc, sys.argv[4] if len(sys.argv) > 4 else None)
    elif cmd == 'nahraj': upload(host, nrc, sys.argv[4] if len(sys.argv) > 4 else 'site', int(sys.argv[5]) if len(sys.argv) > 5 else 4)
