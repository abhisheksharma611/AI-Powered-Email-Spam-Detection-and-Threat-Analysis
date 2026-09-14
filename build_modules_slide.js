/* One-slide academic modules deck - PROFESSIONAL HEADS 2x2. Run: $env:NODE_PATH="<temp>\node_modules"; node build_modules_slide.js */
const pptxgen = require("pptxgenjs");

async function main() {
  const pres = new pptxgen();
  pres.layout = "LAYOUT_16x9"; // 10 x 5.625 - MUST be set before addSlide
  pres.author = "Spam Protection";
  pres.title = "System Modules - Single Slide";

  const NAVY = "1E3A8A", INK = "0F172A", MUTED = "475569", LINE = "CBD5E1";
  const WHITE = "FFFFFF";
  const TINTS = ["EFF6FF", "F0FDF9", "FEF2F2", "F0FDF4"];
  const DOTS = ["2563EB", "0D9488", "B91C1C", "15803D"];

  const slide = pres.addSlide();
  slide.background = { color: WHITE };
  try { slide.transition = { type: "fade", speed: "medium" }; } catch (e) {}

  const shadow = () => ({ type: "outer", color: "64748B", opacity: 0.22, blur: 6, offset: 2, angle: 90 });

  slide.addText("AI-Powered Email Spam Detection  —  System Modules", {
    x: 0.5, y: 0.2, w: 9.0, h: 0.5, fontFace: "Arial", fontSize: 30, bold: true, color: NAVY, margin: 0, valign: "middle",
  });
  slide.addText("Gmail read-only  •  classify → score → display  •  local SQLite", {
    x: 0.5, y: 0.68, w: 9.0, h: 0.3, fontFace: "Arial", fontSize: 13, color: MUTED, margin: 0, valign: "middle",
  });

  const cards = [
    { n: "1", t: "Data Acquisition & Authentication", f: "app.py · config.py · utils/auth.py · gmail_client.py", b: ["Signs in with Google (read-only), downloads latest 50 mails, 20 at a time.", "Runs same pipeline for quick + background scans."] },
    { n: "2", t: "Text Preprocessing & Feature Engineering", f: "gmail_client.py · preprocessing.py", b: ["Opens each mail, decodes it, removes formatting \u2192 plain text.", "Measures 6 spam clues: length, capitals, links, phone, urgency."] },
    { n: "3", t: "Hybrid Classification & Risk Assessment", f: "predictor.py · roberta_model.py · helpers.py", b: ["AI votes (90% RoBERTa + 10% backup) \u2192 1 of 6 categories.", "Urgency + sender past + learnt keywords \u2192 risk 0\u2013100 + reason."] },
    { n: "4", t: "Storage & Visualization", f: "email_model.py · templates/ · static/ · ai_explanation.py", b: ["Saves everything per user in local database (4 tables).", "Shows results table, charts, threat page \u2014 12 pages total."] },
  ];

  const xs = [0.5, 5.05], cw = 4.45, ch = 1.72, ys = [1.12, 2.94];
  cards.forEach((c, i) => {
    const x = xs[i % 2], y = ys[Math.floor(i / 2)];
    slide.addShape("roundRect", { x, y, w: cw, h: ch, fill: { color: TINTS[i] }, line: { color: LINE, width: 0.75 }, rectRadius: 0.12, shadow: shadow() });
    slide.addShape("ellipse", { x: x + 0.2, y: y + 0.16, w: 0.36, h: 0.36, fill: { color: DOTS[i] }, line: { color: DOTS[i], width: 1 } });
    slide.addText(c.n, { x: x + 0.2, y: y + 0.16, w: 0.36, h: 0.36, fontFace: "Arial", fontSize: 14, bold: true, color: WHITE, align: "center", valign: "middle", margin: 0 });
    slide.addText(c.t, { x: x + 0.65, y: y + 0.13, w: 3.6, h: 0.42, fontFace: "Calibri", fontSize: 14, bold: true, color: NAVY, margin: 0, valign: "middle" });
    slide.addText(c.f, { x: x + 0.2, y: y + 0.55, w: 4.05, h: 0.24, fontFace: "Arial", fontSize: 9, color: MUTED, margin: 0, valign: "middle" });
    slide.addText(c.b.map((t, bi) => ({ text: t, options: { fontFace: "Arial", fontSize: 11, color: INK, bullet: true, breakLine: bi < c.b.length - 1, paraSpaceAfter: 6 } })), {
      x: x + 0.2, y: y + 0.84, w: 4.05, h: 0.76, margin: 0, valign: "top",
    });
  });

  slide.addText("6 classes: spam · legitimate · promotion · phishing · malware · newsletter      Risk: Low 0–40 · Med 41–60 · High 61–100", {
    x: 0.5, y: 5.05, w: 9.0, h: 0.35, fontFace: "Arial", fontSize: 11, color: MUTED, margin: 0, valign: "middle",
  });

  slide.addNotes("SAY (60s): Box1: logs in read-only, downloads latest 50 mails. Box2: opens, decodes, cleans to plain text, measures 6 spam clues. Box3: two models vote 1 of 6, then urgency+sender+keywords make 0-100 risk with reason. Box4: saves per user locally, shows table+charts+threat page.\nQ: two models? RoBERTa=meaning, ensemble=cheap backup, 90/10. Q: readonly? cannot send/delete. Q: threats? only spam/phishing/malware. Q: 0-100? base x confidence + boosts, cap 100. Q: models missing? 503, never fakes. Q: limits? 200-500ms/mail CPU, 7-day trend, single worker.\nTIMING: Click1 cards 1-2, Click2 cards 3-4, Click3 footer.");

  await pres.writeFile({ fileName: "C:\\Users\\dell\\Desktop\\Spam Protection 5\\Webapp-Modules-One-Slide.pptx" });
  console.log("WROTE Webapp-Modules-One-Slide.pptx");
}

main().catch((e) => { console.error("BUILD FAILED:", e); process.exit(1); });
