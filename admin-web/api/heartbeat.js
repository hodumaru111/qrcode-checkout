// POST /api/heartbeat — 학생 프로그램이 켜질 때와 6시간마다 보내는 상태 보고.
//
// 학생 PC에는 비밀번호를 둘 수 없으므로 인증이 없다. 대신 이 프로그램의 형식만 받고,
// 모든 항목의 길이와 기록 수에 상한을 둔다.
//
// 저장 위치: 비밀 Gist(AUTH_GIST_ID). 공개 Gist에 두면 학생 PC 이름이 누구에게나 보인다.
// PC마다 파일을 따로 쓴다 (status-<이름 해시>.json). 하나의 파일을 여럿이 읽고 고쳐 쓰면,
// 아침에 여러 대가 동시에 켜질 때 서로의 기록을 덮어써 일부가 사라진다. Gist PATCH는
// 지정한 파일만 바꾸므로 파일을 나누면 그런 일이 없다.
const crypto = require("crypto");

const PREFIX = "status-";
const MAX_PCS = 250; // Gist API가 한 번에 돌려주는 파일 수(300) 안에 들도록

function clean(value, max) {
  return String(value == null ? "" : value)
    .replace(/[\u0000-\u001f\u007f]/g, "")
    .trim()
    .slice(0, max);
}

function fileNameFor(pc) {
  // PC 이름에 한글/공백이 있어도 안전한 파일 이름. 대소문자만 다른 이름은 같은 PC로 본다.
  return PREFIX + crypto.createHash("sha1").update(pc.toLowerCase()).digest("hex").slice(0, 16) + ".json";
}

module.exports = async function handler(req, res) {
  if (req.method !== "POST") {
    res.status(405).json({ error: "POST만 허용됩니다." });
    return;
  }

  const body = req.body || {};
  if (body.app !== "qrcode-checkout") {
    res.status(204).end(); // 이 프로그램이 보낸 것이 아니면 조용히 무시
    return;
  }

  const pc = clean(body.pc, 64);
  if (!pc) {
    res.status(400).json({ error: "pc가 필요합니다." });
    return;
  }

  const gistId = process.env.AUTH_GIST_ID;
  const token = process.env.GITHUB_TOKEN;
  if (!gistId || !token) {
    res.status(500).json({ error: "서버에 AUTH_GIST_ID/GITHUB_TOKEN이 설정되지 않았습니다." });
    return;
  }

  const headers = {
    Authorization: `token ${token}`,
    Accept: "application/vnd.github+json",
    "Content-Type": "application/json",
  };
  const name = fileNameFor(pc);

  try {
    // 처음 보는 PC면 기록 수 상한을 확인한다 (아무나 보고를 보내 Gist를 채우는 것 방지)
    const g = await fetch(`https://api.github.com/gists/${gistId}`, { headers });
    if (!g.ok) throw new Error(`gist 조회 실패 (${g.status})`);
    const files = (await g.json()).files || {};
    const count = Object.keys(files).filter((f) => f.startsWith(PREFIX)).length;
    if (!files[name] && count >= MAX_PCS) {
      res.status(507).json({ error: "기록 가능한 PC 수를 넘었습니다." });
      return;
    }

    const record = {
      pc,
      last_seen: new Date().toISOString(),
      version: clean(body.version, 16),
      last_shown: clean(body.last_shown, 32),
      times: clean(body.times, 120),
      sync_ok: clean(body.sync_ok, 32),
      sync_error: clean(body.sync_error, 160),
    };
    const r = await fetch(`https://api.github.com/gists/${gistId}`, {
      method: "PATCH",
      headers,
      body: JSON.stringify({ files: { [name]: { content: JSON.stringify(record) } } }),
    });
    if (!r.ok) throw new Error(`gist 저장 실패 (${r.status})`);
    res.status(200).json({ ok: true });
  } catch (e) {
    res.status(502).json({ error: String(e.message || e) });
  }
};
