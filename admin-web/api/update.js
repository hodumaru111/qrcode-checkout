// POST /api/update — 비밀번호 인증 후 Gist의 checkout_url/checkout_time을 갱신
const { verifyPassword } = require("./_auth");

const VALID_DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];

module.exports = async function handler(req, res) {
  if (req.method !== "POST") {
    res.status(405).json({ error: "POST만 허용됩니다." });
    return;
  }

  const { password, checkout_time, checkout_times, qr_image, active_days, after_close_url, schedule } =
    req.body || {};

  let passwordOk;
  try {
    passwordOk = password ? await verifyPassword(password) : false;
  } catch (e) {
    res.status(500).json({ error: String(e.message || e) });
    return;
  }
  if (!passwordOk) {
    res.status(401).json({ error: "비밀번호가 올바르지 않습니다." });
    return;
  }

  // 기본 QR: 한 번 등록하면 계속 유지되고, 항목에 따로 이미지가 없으면 이게 표시된다.
  const baseImage = qr_image || "";
  const TIME_RE = /^([01]\d|2[0-3]):[0-5]\d$/;
  const IMAGE_RE = /^data:image\/(png|jpeg|jpg|webp);base64,/;
  const URL_RE = /^https?:\/\//;

  // 새 형식은 시각마다 QR/링크를 따로 가진다. 구 형식(공용 QR + 시각 목록)도 그대로 받는다.
  let entries;
  if (Array.isArray(schedule) && schedule.length) {
    entries = schedule.map((e) => {
      const kind = e && e.kind === "notice" ? "notice" : "checkout";
      return {
        time: String((e && e.time) || "").trim(),
        // 퇴실은 이미지를 비워두면 기본 QR을 쓰고, 공지는 물려받지 않는다.
        kind,
        qr_image: (e && e.qr_image) || "",
        message: String((e && e.message) || "").trim(),
        after_close_url: String((e && e.after_close_url) || "").trim(),
        // 특정 PC에만 보여주는 지정 공지. 퇴실에는 적용하지 않는다 — 퇴실 QR을
        // 한 PC로 좁히면 나머지 전원이 그날 QR을 못 받게 되기 때문이다.
        target_pc: kind === "notice" ? String((e && e.target_pc) || "").trim().slice(0, 64) : "",
      };
    });
  } else {
    const rawTimes = Array.isArray(checkout_times)
      ? checkout_times
      : String(checkout_times || checkout_time || "").split(",");
    entries = [...new Set(rawTimes.map((t) => String(t).trim()).filter(Boolean))].map((t) => ({
      time: t,
      qr_image: qr_image || "",
      after_close_url: String(after_close_url || "").trim(),
    }));
  }

  if (baseImage && !IMAGE_RE.test(baseImage)) {
    res.status(400).json({ error: "기본 QR은 이미지 파일이어야 합니다." });
    return;
  }
  if (!entries.length) {
    res.status(400).json({ error: "퇴실 시각을 최소 하나는 등록하세요." });
    return;
  }
  if (entries.length > 10) {
    res.status(400).json({ error: "퇴실 시각은 최대 10개까지 등록할 수 있습니다." });
    return;
  }

  for (const e of entries) {
    if (!TIME_RE.test(e.time)) {
      res.status(400).json({ error: `퇴실 시각은 HH:MM 형식이어야 합니다: "${e.time}"` });
      return;
    }
    if (e.qr_image && !IMAGE_RE.test(e.qr_image)) {
      res.status(400).json({ error: `${e.time}의 QR은 이미지 파일이어야 합니다.` });
      return;
    }
    if (e.kind === "notice") {
      // 공지는 이미지든 문구든 최소 하나는 있어야 빈 화면이 뜨지 않는다
      if (!e.qr_image && !e.message) {
        res.status(400).json({ error: `${e.time} 공지에 이미지나 안내 문구를 넣으세요.` });
        return;
      }
    } else if (!e.qr_image && !baseImage) {
      res.status(400).json({ error: "기본 QR 이미지를 먼저 등록하세요." });
      return;
    }
    if (e.message.length > 200) {
      res.status(400).json({ error: `${e.time}의 안내 문구가 너무 깁니다 (200자 이내).` });
      return;
    }
    if (e.after_close_url && !URL_RE.test(e.after_close_url)) {
      res.status(400).json({ error: `${e.time}의 링크는 http:// 또는 https://로 시작해야 합니다.` });
      return;
    }
  }

  // 같은 시각 + 같은 대상(전원 또는 동일 PC)이 겹치면 어느 쪽이 뜰지 알 수 없다.
  // 다만 서로 다른 PC를 지정한 공지라면 같은 시각이어도 각자에게만 뜨므로 허용한다.
  const dupeKey = (e) => `${e.time}::${e.target_pc || "ALL"}`;
  const keys = entries.map(dupeKey);
  const dupeIdx = keys.findIndex((k, i) => keys.indexOf(k) !== i);
  if (dupeIdx !== -1) {
    const e = entries[dupeIdx];
    res.status(400).json({
      error: e.target_pc
        ? `${e.time}에 "${e.target_pc}" 대상 공지가 이미 있습니다.`
        : `같은 시각이 두 번 등록되었습니다: ${e.time}`,
    });
    return;
  }

  entries.sort((a, b) => a.time.localeCompare(b.time));

  // Vercel Serverless Function 요청 본문 한도(4.5MB)를 넘지 않도록 여유를 두고 제한
  const totalBytes = baseImage.length + entries.reduce((n, e) => n + e.qr_image.length, 0);
  if (totalBytes > 3_500_000) {
    res.status(400).json({
      error: "등록한 이미지 용량 합계가 너무 큽니다. 더 작은 이미지(스크린샷 크롭 등)를 사용하세요.",
    });
    return;
  }

  let days = VALID_DAYS.slice(0, 5); // 기본값: 월~금
  if (active_days !== undefined) {
    if (!Array.isArray(active_days) || active_days.length === 0 || !active_days.every((d) => VALID_DAYS.includes(d))) {
      res.status(400).json({ error: "active_days는 mon~sun 중 하나 이상을 담은 배열이어야 합니다." });
      return;
    }
    days = active_days;
  }

  const gistId = process.env.GIST_ID;
  const filename = process.env.GIST_FILENAME;
  const token = process.env.GITHUB_TOKEN;
  if (!gistId || !filename || !token) {
    res.status(500).json({ error: "서버에 GIST_ID/GIST_FILENAME/GITHUB_TOKEN이 설정되지 않았습니다." });
    return;
  }

  // 구버전 학생 프로그램(자동 업데이트 전)은 schedule을 모르고 시각 하나만 읽는다.
  // 이때 가장 이른 시각을 주면 점심 퇴실 같은 앞 항목이 대표가 되어, 정작 중요한
  // 마지막 퇴실을 놓친다. 그래서 마지막 시각을 기존 형식으로 남긴다.
  // 구버전 클라이언트는 시각 하나만 읽는다. 공지를 대표로 주면 퇴실을 놓치므로
  // 퇴실 항목 중 마지막 것을 고른다 (퇴실이 하나도 없으면 어쩔 수 없이 마지막 항목).
  const checkouts = entries.filter((e) => e.kind !== "notice");
  const legacy = (checkouts.length ? checkouts : entries)[
    (checkouts.length ? checkouts : entries).length - 1
  ];
  const legacyImage = legacy.qr_image || baseImage;
  const content = JSON.stringify(
    {
      schedule: entries,
      base_qr_image: baseImage,
      qr_image: legacyImage,  // 구버전 학생이 읽는 칸: 실제로 그 시각에 뜨는 이미지
      checkout_time: legacy.time,
      checkout_times: (checkouts.length ? checkouts : entries).map((e) => e.time),
      active_days: days,
      after_close_url: legacy.after_close_url,
    },
    null,
    2
  );

  try {
    const r = await fetch(`https://api.github.com/gists/${gistId}`, {
      method: "PATCH",
      headers: {
        Authorization: `token ${token}`,
        Accept: "application/vnd.github+json",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ files: { [filename]: { content } } }),
    });

    if (!r.ok) {
      const detail = await r.text();
      res.status(502).json({ error: "Gist 업데이트 실패", detail });
      return;
    }

    const result = await r.json();
    res.status(200).json({ ok: true, raw_url: result.files[filename].raw_url });
  } catch (e) {
    res.status(502).json({ error: "Gist 업데이트 중 오류", detail: String(e) });
  }
};
