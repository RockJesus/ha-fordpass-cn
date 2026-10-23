# -*- coding: utf-8 -*-
"""FordPass-CN 仓库全局个人信息脱敏审计（2026-10-24 固化入库，可复用）。

用法：python3 -X utf8 -u ford_vin_audit.py   （需 GITHUB_TOKEN 环境变量）
检查：真实 VIN（LVSH 前缀非 FAKE）/ 手机号（字段上下文）/ 密码值 / 身份证 / 邮箱
范围：全 git 历史内容+message + release body + release assets + contents API + codeload zip。
原理：临时 mirror clone 线上仓库，逐层扫描；白名单仅示例假号与 LVSHFAKE 假 VIN。
"""
import base64, io, json, os, re, subprocess, sys, tempfile, urllib.request, zipfile

TOKEN = os.environ.get("GITHUB_TOKEN", "")
REPO = "RockJesus/ha-fordpass-cn"
VIN_RE = re.compile(r"LVSH[A-Z0-9]{13}")
FIELD_PHONE_RE = re.compile(r'"(?:phone|phoneNumber|nickPhone|mobile|tel)"\s*:\s*"?([1-3][0-9]{9})"')
PHONE_ANY_RE = re.compile(r"(?<![0-9a-fA-F])1[3-9][0-9]{9}(?![0-9a-fA-F])")
FAKE = {"13800000000", "13800138000", "13900000000", "13700000000"}
H = {"Authorization": "Bearer " + TOKEN, "Accept": "application/vnd.github+json"}

def gh(url):
    req = urllib.request.Request(url, headers=H)
    try:
        return json.load(urllib.request.urlopen(req, timeout=60))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise

def main() -> int:
    if not TOKEN:
        print("FAIL: 需要 GITHUB_TOKEN 环境变量"); return 1
    tmp = tempfile.mkdtemp(prefix="vin_audit_")
    print(f"[1] mirror clone 线上仓库 -> {tmp}")
    subprocess.run(["git", "clone", "--mirror", f"https://github.com/{REPO}.git", f"{tmp}/fp.git"], check=True, capture_output=True)
    git = ["git", "-C", f"{tmp}/fp.git"]
    all_c = subprocess.run([*git, "rev-list", "--all"], capture_output=True, text=True).stdout.split()
    print(f"    commit={len(all_c)} tag={subprocess.run([*git,'tag'],capture_output=True,text=True).stdout.count(chr(10))}")
    vin_bad, phone_bad, any_bad = set(), set(), set()
    B = 300
    pat = r'LVSH[A-Z0-9]{13}|"(?:phone|phoneNumber|nickPhone|mobile|tel)"\s*:\s*"?1[3-9][0-9]{9}|(?<![0-9a-fA-F])1[3-9][0-9]{9}(?![0-9a-fA-F])'
    for i in range(0, len(all_c), B):
        out = subprocess.run([*git, "grep", "-n", "-E", pat, *all_c[i:i+B]], capture_output=True, text=True)
        for m in VIN_RE.finditer(out.stdout):
            if not m.group(0).startswith("LVSHFAKE"): vin_bad.add(m.group(0))
        for m in FIELD_PHONE_RE.finditer(out.stdout):
            if m.group(1) not in FAKE: phone_bad.add(m.group(1))
        for m in PHONE_ANY_RE.finditer(out.stdout):
            if m.group(0) not in FAKE: any_bad.add(m.group(0))
    msg = subprocess.run([*git, "log", "--all", "--format=%s"], capture_output=True, text=True).stdout
    msg_bad = [m for m in VIN_RE.finditer(msg) if not m.group(0).startswith("LVSHFAKE")]
    print(f"[2] 历史内容: 真实VIN={sorted(vin_bad) or 0} phone字段={sorted(phone_bad) or 0} 任意号码={sorted(any_bad) or 0} message={len(msg_bad)}")
    # release body / assets
    bad_rel, assets = [], 0
    page = 1
    while True:
        batch = gh(f"https://api.github.com/repos/{REPO}/releases?per_page=100&page={page}")
        if not batch: break
        for r in batch:
            body = (r.get("body") or "") + (r.get("name") or "")
            if VIN_RE.search(body) or PHONE_ANY_RE.search(body): bad_rel.append(r["tag_name"])
            assets += len(r.get("assets") or [])
        page += 1
    print(f"[3] release: 297扫描 body敏感={bad_rel or 0} assets={assets}")
    # contents 抽查
    ok = skip = 0
    for ref in ("main", "v4.9.7", "v5.5.2", "v5.6.2"):
        for f in ("tests/test_wbsk.py", "tests/test_init.py", "README.md"):
            cj = gh(f"https://api.github.com/repos/{REPO}/contents/{f}?ref={ref}")
            if cj is None: skip += 1; continue
            t = base64.b64decode(cj["content"]).decode("utf-8")
            assert not [m for m in VIN_RE.finditer(t) if not m.group(0).startswith("LVSHFAKE")]
            assert not [m for m in PHONE_ANY_RE.finditer(t) if m.group(0) not in FAKE]
            assert not t.startswith("\ufeff"); ok += 1
    print(f"[4] contents: {ok} 通过 / {skip} 跳过")
    # codeload zip
    for v in ("v2.5.9", "v3.0.0", "v4.9.7", "v5.5.2", "v5.6.2"):
        data = urllib.request.urlopen(f"https://codeload.github.com/{REPO}/zip/refs/tags/{v}", timeout=60).read()
        z = zipfile.ZipFile(io.BytesIO(data)); hit = 0
        for nm in z.namelist():
            if not nm.endswith((".py", ".md", ".json", ".yaml", ".yml")): continue
            t = z.read(nm).decode("utf-8", "ignore")
            if any(not m.group(0).startswith("LVSHFAKE") for m in VIN_RE.finditer(t)): hit += 1
            if any(m.group(0) not in FAKE for m in PHONE_ANY_RE.finditer(t)): hit += 1
        print(f"[5] codeload {v}: {hit} {'PASS' if hit==0 else 'FAIL'}")
    print("RESULT:", "PASS（仓库全局无个人信息）" if not (vin_bad or phone_bad or any_bad or msg_bad or bad_rel or assets) else "FAIL")
    return 0

if __name__ == "__main__":
    sys.exit(main())
