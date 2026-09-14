/* Use case diagram - bright, clean labels (no route tags).
   Run: $env:NODE_PATH="<temp>\node_modules"; node build_usecase_slide.js */
const pptxgen = require("pptxgenjs");

async function main() {
  const pres = new pptxgen();
  pres.layout = "LAYOUT_16x9"; // 10 x 5.625 - MUST be set before addSlide
  pres.author = "Spam Protection";
  pres.title = "Use Case Diagram - Spam Protection Webapp";

  const INK = "1F1F1F", GRAY = "808080";
  const BG = "F2F2F2", BOUND = "FFFFFF", OVAL = "FFFFFF";
  const DARK = "1D1333", TITLEC = "E0507C", SQ = "A12556", BLACK = "111111";
  const slide = pres.addSlide();
  slide.background = { color: BG };
  try { slide.transition = { type: "fade", speed: "medium" }; } catch (e) {}

  // Header band + bright title + maroon square
  slide.addShape("rect", { x: 0, y: 0, w: 10, h: 1.0, fill: { color: DARK }, line: { color: DARK, width: 1 } });
  slide.addShape("rect", { x: 9.3, y: 0, w: 0.7, h: 0.62, fill: { color: SQ }, line: { color: SQ, width: 1 } });
  slide.addText("USE CASE DIAGRAM", { x: 0.6, y: 0.18, w: 8.4, h: 0.6, fontFace: "Arial", fontSize: 32, bold: true, color: TITLEC, margin: 0, valign: "middle" });

  // Geometry (mirrors HTML)
  const BX = 3.35, BW = 3.3, BY = 1.14, BH = 3.6;
  const OX = 3.55, OW = 2.9, OH = 0.36;
  const oy = [1.3, 1.72, 2.14, 2.56, 2.98, 3.4, 3.82, 4.24];
  const mids = oy.map((y) => y + OH / 2);

  // Straight connector = thin rotated rect (always valid, exact angle)
  const link = (x1, y1, x2, y2, color) => {
    const dx = x2 - x1, dy = y2 - y1;
    const len = Math.hypot(dx, dy);
    const ang = (Math.atan2(dy, dx) * 180) / Math.PI;
    const t = 0.025;
    slide.addShape("rect", {
      x: (x1 + x2) / 2 - len / 2, y: (y1 + y2) / 2 - t / 2,
      w: len, h: t, fill: { color }, line: { color, width: 1 }, rotate: ang,
    });
  };

  // 1) Connectors FIRST (boundary + ovals cover inner segments)
  mids.forEach((m) => link(1.85, 2.95, OX, m, INK));
  [0, 1, 2, 4].forEach((i) => link(8.05, 2.95, OX + OW, mids[i], GRAY));

  // 2) Boundary
  slide.addShape("rect", { x: BX, y: BY, w: BW, h: BH, fill: { color: BOUND }, line: { color: INK, width: 1.5 } });

  // 3) Ovals, single clean labels
  const cases = [
    "Login / Logout", "Scan inbox now", "Scan in background", "Browse results",
    "Open mail verdict", "Check pasted text", "View analytics", "Threat console",
  ];
  cases.forEach((t, i) => {
    slide.addShape("ellipse", { x: OX, y: oy[i], w: OW, h: OH, fill: { color: OVAL }, line: { color: INK, width: 1.25 } });
    slide.addText(t, { x: OX, y: oy[i], w: OW, h: OH, fontFace: "Arial", fontSize: 12, bold: true, color: INK, align: "center", valign: "middle", margin: 0 });
  });

  // 4) Actors on top
  const actor = (hxc, lx, name, sub) => {
    slide.addShape("ellipse", { x: hxc - 0.12, y: 2.5, w: 0.24, h: 0.24, fill: { color: BG }, line: { color: INK, width: 2 } });
    slide.addShape("rect", { x: hxc - 0.017, y: 2.74, w: 0.035, h: 0.62, fill: { color: INK }, line: { color: INK, width: 1 } });
    slide.addShape("rect", { x: hxc - 0.31, y: 2.93, w: 0.62, h: 0.035, fill: { color: INK }, line: { color: INK, width: 1 } });
    const hipY = 3.36, footY = 3.88, spread = 0.26;
    const legL = Math.hypot(spread, footY - hipY), angL = (Math.atan2(footY - hipY, -spread) * 180) / Math.PI;
    const legR = Math.hypot(spread, footY - hipY), angR = (Math.atan2(footY - hipY, spread) * 180) / Math.PI;
    slide.addShape("rect", { x: (hxc + hxc - spread) / 2 - legL / 2, y: (hipY + footY) / 2 - 0.017, w: legL, h: 0.035, fill: { color: INK }, line: { color: INK, width: 1 }, rotate: angL });
    slide.addShape("rect", { x: (hxc + hxc + spread) / 2 - legR / 2, y: (hipY + footY) / 2 - 0.017, w: legR, h: 0.035, fill: { color: INK }, line: { color: INK, width: 1 }, rotate: angR });
    slide.addText(name, { x: lx, y: 3.95, w: 1.3, h: 0.28, fontFace: "Arial", fontSize: 12, bold: true, color: INK, align: "center", margin: 0 });
    if (sub) slide.addText(sub, { x: lx, y: 4.21, w: 1.3, h: 0.24, fontFace: "Arial", fontSize: 8, color: "333333", align: "center", margin: 0 });
  };
  actor(1.67, 1.02, "User", null);
  actor(8.27, 7.62, "Google Services", "OAuth · Gmail · NIM");

  // Caption + black footer bar + maroon block
  slide.addText("dark lines = you use · gray lines = Google side (login · mails · explanation)", {
    x: 0.5, y: 4.82, w: 9.0, h: 0.24, fontFace: "Arial", fontSize: 9, color: "333333", align: "center", margin: 0, valign: "middle",
  });
  slide.addShape("rect", { x: 0, y: 5.25, w: 10, h: 0.375, fill: { color: BLACK }, line: { color: BLACK, width: 1 } });
  slide.addShape("rect", { x: 9.3, y: 5.25, w: 0.7, h: 0.375, fill: { color: SQ }, line: { color: SQ, width: 1 } });

  slide.addNotes("SAY: One human actor, the logged-in Gmail owner — every feature route is @login_required and threat-console has no role check. Google side authenticates, supplies the 50 mails, optionally writes one explanation. Core path: login, scan, open a verdict, check analytics. ADMIN_EMAILS gates nothing — proposed extension only.");
  await pres.writeFile({ fileName: "C:\\Users\\dell\\Desktop\\Spam Protection 5\\UseCase-Diagram-One-Slide.pptx" });
  console.log("WROTE UseCase-Diagram-One-Slide.pptx");
}

main().catch((e) => { console.error("BUILD FAILED:", e); process.exit(1); });
