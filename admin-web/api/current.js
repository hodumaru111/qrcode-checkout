// GET /api/current — 현재 Gist에 등록된 QR 설정을 반환 (인증 불필요, 읽기 전용)
//
// 예전에는 GIST_RAW_URL(gist.githubusercontent.com/<계정이름>/<id>/raw/...)을 읽었다.
// 그 주소에는 GitHub 계정 이름이 들어 있어서, 계정 이름을 바꾸자 404가 나며 관리자
// 페이지가 통째로 안 열렸다. 저장(update.js)처럼 Gist ID + 토큰으로 GitHub API를 읽으면
// 계정 이름과 무관하게 동작하고, CDN 캐시 지연도 없다.
module.exports = async function handler(req, res) {
  if (req.method !== "GET") {
    res.status(405).json({ error: "GET만 허용됩니다." });
    return;
  }

  const gistId = process.env.GIST_ID;
  const filename = process.env.GIST_FILENAME;
  const token = process.env.GITHUB_TOKEN;
  if (!gistId || !filename || !token) {
    res.status(500).json({ error: "서버에 GIST_ID/GIST_FILENAME/GITHUB_TOKEN이 설정되지 않았습니다." });
    return;
  }

  try {
    const headers = { Authorization: `token ${token}`, Accept: "application/vnd.github+json" };
    const r = await fetch(`https://api.github.com/gists/${gistId}`, { headers, cache: "no-store" });
    if (!r.ok) throw new Error(`gist api ${r.status}`);
    const gist = await r.json();
    const file = gist.files && gist.files[filename];
    if (!file) throw new Error(`gist에 ${filename} 파일이 없습니다`);

    // 파일이 크면 API가 content를 잘라서 준다. 그때는 raw_url(현재 계정 이름이 들어간
    // 최신 주소)에서 전체를 받는다.
    let content = file.content;
    if (file.truncated) {
      const raw = await fetch(file.raw_url, { headers: { Authorization: `token ${token}` }, cache: "no-store" });
      if (!raw.ok) throw new Error(`gist raw ${raw.status}`);
      content = await raw.text();
    }

    res.setHeader("Cache-Control", "no-store");
    res.status(200).json(JSON.parse(content));
  } catch (e) {
    res.status(502).json({ error: "현재 설정을 불러오지 못했습니다.", detail: String(e) });
  }
};
