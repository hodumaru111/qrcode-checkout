// POST /api/status — 관리자가 '설치 현황'을 조회한다. 학생 PC 이름이 담기므로 비밀번호를 확인한다.
// 응답: { records: [{pc, last_seen, version, last_shown, times, sync_ok, sync_error}], latest }
const { verifyPassword } = require("./_auth");

const PREFIX = "status-";
const VERSION_URL = "https://raw.githubusercontent.com/hodumaru111/qrcode-checkout/main/version.json";

module.exports = async function handler(req, res) {
  if (req.method !== "POST") {
    res.status(405).json({ error: "POST만 허용됩니다." });
    return;
  }

  const { password } = req.body || {};
  let ok;
  try {
    ok = password ? await verifyPassword(password) : false;
  } catch (e) {
    res.status(500).json({ error: String(e.message || e) });
    return;
  }
  if (!ok) {
    res.status(401).json({ error: "비밀번호가 올바르지 않습니다." });
    return;
  }

  const gistId = process.env.AUTH_GIST_ID;
  const token = process.env.GITHUB_TOKEN;
  if (!gistId || !token) {
    res.status(500).json({ error: "서버에 AUTH_GIST_ID/GITHUB_TOKEN이 설정되지 않았습니다." });
    return;
  }

  try {
    const auth = { Authorization: `token ${token}` };
    const g = await fetch(`https://api.github.com/gists/${gistId}`, {
      headers: { ...auth, Accept: "application/vnd.github+json" },
      cache: "no-store",
    });
    if (!g.ok) throw new Error(`조회 실패 (${g.status})`);
    const files = (await g.json()).files || {};

    const records = [];
    for (const [name, file] of Object.entries(files)) {
      if (!name.startsWith(PREFIX)) continue;
      let content = file.content;
      if (file.truncated) {
        const raw = await fetch(file.raw_url, { headers: auth });
        if (!raw.ok) continue;
        content = await raw.text();
      }
      try {
        records.push(JSON.parse(content));
      } catch {
        // 깨진 기록 하나 때문에 전체가 안 보이면 안 된다
      }
    }

    // 지금 배포 중인 버전 (구버전 PC를 표시하는 기준)
    let latest = null;
    try {
      const v = await fetch(`${VERSION_URL}?t=${Date.now()}`, { cache: "no-store" });
      if (v.ok) latest = (await v.json()).version || null;
    } catch {
      latest = null;
    }

    res.setHeader("Cache-Control", "no-store");
    res.status(200).json({ records, latest });
  } catch (e) {
    res.status(502).json({ error: String(e.message || e) });
  }
};
