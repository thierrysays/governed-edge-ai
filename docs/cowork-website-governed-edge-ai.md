# Cowork Brief: governed-edge-ai.html

**Target page**: `https://www.glossolalie.pro/governed-edge-ai.html`  
**Task**: Create this page from scratch (it does not exist yet) and commit the file as part of the site's flat `public_html/` deployment package.

---

## Context

Thierry Sayegh (Glossolalie Advisory, Paris) is publishing a technical case study titled **"Governed Physical AI"**, an open-source demonstration that governance and audit controls identical to those required by the EU AI Act can be implemented end-to-end on commodity embedded hardware costing under EUR 200.

The project repository is `https://github.com/thierrysays/governed-edge-ai` (public). The page is a project showcase page on `glossolalie.pro`, not a blog article. It should read as a senior practitioner's note to peers, not a tutorial aimed at beginners.

---

## Site conventions: MANDATORY (read before writing a single line of HTML)

The site uses a strict no-framework stack. Violating any of these will break the page or the toggle.

| Rule | Value |
|---|---|
| HTML structure | Vanilla HTML5, no framework, no CMS, no build step |
| Scripts | `<script src="/assets/js/i18n.js?v=4"></script>` and `<script src="/assets/js/main.js?v=4" defer></script>`. NEVER `type="module"` |
| Bilingual | All user-visible text in `data-i18n` (plain text) or `data-i18n-html` (HTML with tags). Never hardcode text in FR or EN only. |
| Entities in `data-i18n` | Forbidden: `data-i18n` uses `textContent`, so `&amp;` renders as `&amp;`. Write `&` and `>` directly. |
| Em-dash | Banned everywhere: in HTML, in `data-i18n` values, in JS keys. Replace with colon, comma, or period. |
| CSS | `/assets/css/tokens.css?v=4` then `/assets/css/main.css?v=4`. No inline styles except for page-specific overrides in a `<style>` block. |
| Favicon | `<link rel="icon" href="/assets/img/favicon.ico">` |
| Nav / footer | Copy the nav and footer blocks verbatim from an existing page (e.g. `frameworks.html`). Do not invent new nav links. |
| `data-page` | Set `data-page="governed-edge-ai"` on `<body>` so the active nav item highlights correctly. |

---

## Page structure

Use the following section order, mirroring the `frameworks.html` page layout:

1. `<head>` with title, description, canonical, og tags, CSS links
2. `<header class="site-nav">`: copy from existing page
3. `<main>`
   - `<section class="page-header">` (hero / title)
   - `<section class="section section--light">` (project summary)
   - `<section class="section section--dark">` (architecture)
   - `<section class="section section--light">` (build steps, numbered 1-8)
   - `<section class="section section--dark">` (governance controls)
   - `<section class="section section--light">` (QA baseline and results)
   - `<section class="section section--dark">` (repository and reuse)
4. `<footer class="site-footer">`: copy from existing page

---

## i18n keys to add to `assets/js/i18n.js`

Add these keys to BOTH the `fr` and `en` translation objects. Insert them together, after the last existing key of each language object. Never touch existing keys.

Use straight apostrophes `\'` (escaped) inside JS single-quoted strings. No curly apostrophes.

```js
// ---- governed-edge-ai page ----
'edge.meta.title':       { fr: 'Governed Physical AI', en: 'Governed Physical AI' },
'edge.meta.desc':        { fr: 'Etude de cas : gouvernance IA embarquee sur trois cartes Arduino pour moins de 200 EUR', en: 'Case study: AI governance on three Arduino boards for under EUR 200' },
'edge.hero.title':       { fr: 'Governed Physical AI', en: 'Governed Physical AI' },
'edge.hero.sub':         { fr: 'Peut-on implementer les controles requis par l\'AI Act sur du materiel embarque grand public ? Cette etude de cas repond oui.', en: 'Can EU AI Act controls be implemented on consumer embedded hardware? This case study answers yes.' },
'edge.hero.label':       { fr: 'Etude de cas', en: 'Case study' },
'edge.summary.h2':       { fr: 'De quoi s\'agit-il ?', en: 'What is this?' },
'edge.summary.p1':       { fr: 'Trois cartes Arduino connectees forment un robot mobile gouverne : une camera de perception, un filtre de gouvernance avec journal d\'audit, et un microcontroleur physique. Chaque commande moteur est tracee dans une base SQLite avant d\'etre transmise. Aucune commande ne peut contourner l\'audit.', en: 'Three connected Arduino boards form a governed mobile robot: a perception camera, a governance filter with audit log, and a physical microcontroller. Every motor command is recorded in a SQLite database before transmission. No command can bypass the audit.' },
'edge.summary.p2':       { fr: 'Le code est open source, les tests sont executes en CI sans materiel physique, et la couverture de code depasse 95%. Le projet peut etre reproduit pour moins de 200 EUR.', en: 'The code is open source, tests run in CI without physical hardware, and code coverage exceeds 95%. The project can be reproduced for under EUR 200.' },
'edge.summary.cost':     { fr: 'Cout du materiel', en: 'Hardware cost' },
'edge.summary.costval':  { fr: 'Moins de 200 EUR', en: 'Under EUR 200' },
'edge.summary.boards':   { fr: 'Cartes', en: 'Boards' },
'edge.summary.boardsval':{ fr: '3 Arduino', en: '3 Arduino' },
'edge.summary.tests':    { fr: 'Tests automatises', en: 'Automated tests' },
'edge.summary.testsval': { fr: '241 tests, 95.76% couverture', en: '241 tests, 95.76% coverage' },
'edge.arch.h2':          { fr: 'Architecture trois cartes', en: 'Three-board architecture' },
'edge.arch.p1':          { fr: 'Le flux de donnees est unidirectionnel et ordonne : la perception ne commande jamais directement l\'actuateur. Chaque couche a une responsabilite unique et ne depend pas des couches en aval.', en: 'The data flow is unidirectional and ordered: perception never commands the actuator directly. Each layer has a single responsibility and does not depend on downstream layers.' },
'edge.arch.uno':         { fr: 'Arduino UNO Q 4GB', en: 'Arduino UNO Q 4GB' },
'edge.arch.uno.role':    { fr: 'Perception : capture camera V4L2, inference YOLO-X / MediaPipe / PoseNet, envoi des detections via TCP', en: 'Perception: V4L2 camera capture, YOLO-X / MediaPipe / PoseNet inference, sends detections via TCP' },
'edge.arch.ventuno':     { fr: 'Arduino VENTUNO Q', en: 'Arduino VENTUNO Q' },
'edge.arch.ventuno.role':{ fr: 'Gouvernance : filtre de confiance, journal d\'audit SQLite (log-before-act), encodage IPC, envoi des commandes via USB-C serie', en: 'Governance: confidence filter, SQLite audit log (log-before-act), IPC encoding, sends commands via USB-C serial' },
'edge.arch.alvik':       { fr: 'Arduino Alvik', en: 'Arduino Alvik' },
'edge.arch.alvik.role':  { fr: 'Corps physique : deuxieme couche de gouvernance firmware, commandes moteur, kill switch materiel, ACK/REJECT retourne', en: 'Physical body: second firmware governance layer, motor commands, hardware kill switch, ACK/REJECT returned' },
'edge.arch.proto':       { fr: 'Protocole IPC', en: 'IPC protocol' },
'edge.arch.proto.val':   { fr: 'Binaire CRC-16/CCITT sur UART serie, 8 types de message, max 261 octets', en: 'Binary CRC-16/CCITT over serial UART, 8 message types, max 261 bytes' },
'edge.steps.h2':         { fr: 'Les 8 etapes de construction', en: '8 build steps' },
'edge.steps.intro':      { fr: 'Le projet est construit par increments tests. Chaque etape produit du code fonctionnel avec des tests passants avant de passer a la suivante.', en: 'The project is built in tested increments. Each step produces working code with passing tests before moving to the next.' },
'edge.step1.title':      { fr: 'Etape 1 : Protocole IPC et codec binaire', en: 'Step 1: IPC protocol and binary codec' },
'edge.step1.body':       { fr: 'Definition du format de trame binaire (CRC-16, little-endian, 8 types). Codec Python encode/decode. 45 tests. Base commune a toutes les couches.', en: 'Binary frame format defined (CRC-16, little-endian, 8 types). Python encode/decode codec. 45 tests. Shared base for all layers.' },
'edge.step2.title':      { fr: 'Etape 2 : Journal d\'audit SQLite', en: 'Step 2: SQLite audit log' },
'edge.step2.body':       { fr: 'Schema SQLite avec contraintes CHECK sur detection_type. AuditLogger thread-safe avec WAL. 45 tests. Invariant log-before-act etabli.', en: 'SQLite schema with CHECK constraints on detection_type. Thread-safe AuditLogger with WAL. 45 tests. Log-before-act invariant established.' },
'edge.step3.title':      { fr: 'Etape 3 : Heartbeat et supervision', en: 'Step 3: Heartbeat and supervision' },
'edge.step3.body':       { fr: 'HeartbeatMonitor avec watchdog 500ms. HaltNotify automatique si le canal se coupe. Tests de liveness et de timeout.', en: 'HeartbeatMonitor with 500ms watchdog. Automatic HaltNotify if channel drops. Liveness and timeout tests.' },
'edge.step4.title':      { fr: 'Etape 4 : Pair STM32H5 simule', en: 'Step 4: Simulated STM32H5 peer' },
'edge.step4.body':       { fr: 'MockSTM32H5 sur pseudo-terminal pty. Machine d\'etat ARMED / BUSY / HALTED / FAULT. Cinq priorites de rejet dans l\'ordre exact du firmware.', en: 'MockSTM32H5 on pty pseudo-terminal. ARMED / BUSY / HALTED / FAULT state machine. Five rejection priorities in exact firmware order.' },
'edge.step5.title':      { fr: 'Etape 5 : Interface de perception', en: 'Step 5: Perception interface' },
'edge.step5.body':       { fr: 'DetectionResult frozen dataclass. PerceptionPipeline ABC. Backends stub : StubObjectDetector, StubGestureRecognizer, StubPoseEstimator, NullPipeline. 46 tests.', en: 'DetectionResult frozen dataclass. PerceptionPipeline ABC. Stub backends: StubObjectDetector, StubGestureRecognizer, StubPoseEstimator, NullPipeline. 46 tests.' },
'edge.step6.title':      { fr: 'Etape 6 : Filtre de gouvernance', en: 'Step 6: Governance filter' },
'edge.step6.body':       { fr: 'GovernanceFilter : seuil de confiance 0.70, log-before-act, une commande par trame, fallback HALT sur label inconnu. 36 tests + 7 smoke tests.', en: 'GovernanceFilter: 0.70 confidence threshold, log-before-act, one command per frame, HALT fallback on unknown label. 36 tests plus 7 smoke tests.' },
'edge.step7.title':      { fr: 'Etape 7 : Firmware Alvik (MicroPython)', en: 'Step 7: Alvik firmware (MicroPython)' },
'edge.step7.body':       { fr: 'Codec IPC MicroPython testable sous CPython. Quatre portes de gouvernance firmware : audit_ref, kill switch, seuil confiance float32, action valide. Commandes moteur via arduino_alvik.', en: 'MicroPython IPC codec testable under CPython. Four firmware governance gates: audit_ref, kill switch, float32 confidence threshold, valid action. Motor commands via arduino_alvik.' },
'edge.step8.title':      { fr: 'Etape 8 : Services UNO Q et VENTUNO Q', en: 'Step 8: UNO Q and VENTUNO Q services' },
'edge.step8.body':       { fr: 'PerceptionService (UNO Q) : capture multi-backend avec fallback stub. GovernanceService (VENTUNO Q) : reception TCP, filtre, dispatch IPC. Transport JSON prefixe par longueur sur TCP.', en: 'PerceptionService (UNO Q): multi-backend capture with stub fallback. GovernanceService (VENTUNO Q): TCP receive, filter, IPC dispatch. Length-prefixed JSON over TCP transport.' },
'edge.gov.h2':           { fr: 'Controles de gouvernance implementes', en: 'Governance controls implemented' },
'edge.gov.intro':        { fr: 'Six invariants de securite sont appliques en code, pas en processus. Aucun d\'entre eux ne peut etre contourne par un bug applicatif.', en: 'Six safety invariants are enforced in code, not process. None can be bypassed by an application bug.' },
'edge.gov.g1':           { fr: 'Log-before-act : l\'audit_ref SQLite est obtenu avant toute transmission de CommandRequest. Echec de journalisation = aucune commande envoyee.', en: 'Log-before-act: SQLite audit_ref obtained before any CommandRequest transmission. Logging failure means no command sent.' },
'edge.gov.g2':           { fr: 'Double couche de confiance : seuil 0.70 float64 cote Linux, seuil 0.70 float32 cote firmware. La valeur exacte 0.70 est attrapee par la seconde couche apres arrondi IEEE 754.', en: 'Dual confidence gate: 0.70 float64 on Linux side, 0.70 float32 on firmware side. Exact value 0.70 is caught by the second layer after IEEE 754 rounding.' },
'edge.gov.g3':           { fr: 'audit_ref zero interdit : les deux couches rejettent un CommandRequest avec audit_ref == 0. Priorite absolue, verifie avant tout autre test.', en: 'Zero audit_ref rejected: both layers reject a CommandRequest with audit_ref == 0. Absolute priority, checked before any other test.' },
'edge.gov.g4':           { fr: 'Kill switch materiel : bouton physique sur GPIO, polling 50ms. Declenche l\'etat HALTED qui rejette toutes les commandes suivantes.', en: 'Hardware kill switch: physical button on GPIO, polled every 50ms. Triggers HALTED state, rejecting all subsequent commands.' },
'edge.gov.g5':           { fr: 'Fallback HALT sur label inconnu : le filtre de gouvernance emet toujours HALT pour un label non repertorie. Le systeme ne peut pas ignorer une detection inconnue.', en: 'HALT fallback on unknown label: governance filter always emits HALT for an unlisted label. The system cannot ignore an unknown detection.' },
'edge.gov.g6':           { fr: 'Watchdog heartbeat 500ms : si le canal IPC se coupe, HaltNotify est envoye automatiquement avant l\'expiration du watchdog.', en: '500ms heartbeat watchdog: if the IPC channel drops, HaltNotify is sent automatically before the watchdog expires.' },
'edge.qa.h2':            { fr: 'Qualite et reproductibilite', en: 'Quality and reproducibility' },
'edge.qa.p1':            { fr: 'Tous les tests s\'executent sans materiel physique. L\'environnement CI reproduit la pile complete : synthese de trames, pty simule, transport TCP en loopback.', en: 'All tests run without physical hardware. The CI environment reproduces the full stack: synthetic frames, simulated pty, loopback TCP transport.' },
'edge.qa.tests':         { fr: '241 tests automatises', en: '241 automated tests' },
'edge.qa.cov':           { fr: '95.76% de couverture de code', en: '95.76% code coverage' },
'edge.qa.linters':       { fr: 'Ruff, mypy strict, bandit SAST, pip-audit CVE', en: 'Ruff, mypy strict, bandit SAST, pip-audit CVE' },
'edge.qa.ci':            { fr: 'GitHub Actions, aucun materiel requis', en: 'GitHub Actions, no hardware required' },
'edge.repo.h2':          { fr: 'Depot et reutilisation', en: 'Repository and reuse' },
'edge.repo.p1':          { fr: 'Le code est publie sous licence MIT. Chaque couche (codec IPC, journal d\'audit, filtre de gouvernance, firmware MicroPython) peut etre reutilisee independamment dans un autre projet.', en: 'Code is published under the MIT license. Each layer (IPC codec, audit log, governance filter, MicroPython firmware) can be reused independently in another project.' },
'edge.repo.link':        { fr: 'Voir le depot GitHub', en: 'View GitHub repository' },
'edge.repo.note':        { fr: 'Documentation d\'architecture complete dans docs/architecture.md', en: 'Full architecture documentation in docs/architecture.md' },
```

---

## HTML page content

Write the following HTML file as `governed-edge-ai.html` at the root of `public_html/`. Use the nav and footer from `frameworks.html` verbatim (fetch the live page to get the exact markup).

```html
<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title data-i18n="edge.meta.title">Governed Physical AI</title>
  <meta name="description" data-i18n="edge.meta.desc" content="">
  <link rel="canonical" href="https://www.glossolalie.pro/governed-edge-ai.html">
  <meta property="og:title" data-i18n="edge.meta.title" content="Governed Physical AI">
  <meta property="og:url" content="https://www.glossolalie.pro/governed-edge-ai.html">
  <meta property="og:type" content="website">
  <link rel="icon" href="/assets/img/favicon.ico">
  <link rel="stylesheet" href="/assets/css/tokens.css?v=4">
  <link rel="stylesheet" href="/assets/css/main.css?v=4">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap">
  <style>
    .edge-board-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
      gap: var(--space-lg);
      margin-top: var(--space-lg);
    }
    .edge-board-card {
      background: var(--color-ivory);
      border-radius: var(--radius-md);
      padding: var(--space-lg);
      border-top: 3px solid var(--color-teal);
    }
    .edge-board-card h3 {
      color: var(--color-navy);
      font-size: var(--text-base);
      font-weight: 600;
      margin-bottom: var(--space-sm);
    }
    .edge-steps-list {
      counter-reset: step;
      list-style: none;
      padding: 0;
      margin: var(--space-lg) 0 0;
    }
    .edge-steps-list li {
      counter-increment: step;
      display: grid;
      grid-template-columns: 2.5rem 1fr;
      gap: var(--space-md);
      margin-bottom: var(--space-lg);
      align-items: start;
    }
    .edge-steps-list li::before {
      content: counter(step);
      background: var(--color-teal);
      color: #fff;
      border-radius: 50%;
      width: 2.5rem;
      height: 2.5rem;
      display: flex;
      align-items: center;
      justify-content: center;
      font-weight: 700;
      font-size: var(--text-sm);
      flex-shrink: 0;
    }
    .edge-step-title {
      font-weight: 600;
      color: var(--color-navy);
      margin-bottom: var(--space-xs);
    }
    .edge-gov-list {
      list-style: none;
      padding: 0;
      margin: var(--space-lg) 0 0;
    }
    .edge-gov-list li {
      padding: var(--space-md) var(--space-lg);
      border-left: 3px solid var(--color-teal);
      background: rgba(13,148,136,.06);
      border-radius: 0 var(--radius-sm) var(--radius-sm) 0;
      margin-bottom: var(--space-sm);
      font-size: var(--text-sm);
      line-height: 1.6;
    }
    .edge-stats-row {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: var(--space-md);
      margin: var(--space-lg) 0;
    }
    .edge-stat {
      text-align: center;
      padding: var(--space-lg);
      background: var(--color-ivory);
      border-radius: var(--radius-md);
    }
    .edge-stat__value {
      font-size: var(--text-2xl);
      font-weight: 700;
      color: var(--color-teal);
      display: block;
    }
    .edge-stat__label {
      font-size: var(--text-xs);
      color: var(--color-text-muted);
      margin-top: var(--space-xs);
    }
    .edge-summary-grid {
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: var(--space-md);
      margin: var(--space-lg) 0;
    }
    @media (max-width: 640px) {
      .edge-summary-grid { grid-template-columns: 1fr; }
    }
    .edge-summary-item {
      padding: var(--space-md);
      border-radius: var(--radius-sm);
      background: var(--color-ivory);
      border-top: 2px solid var(--color-teal);
    }
    .edge-summary-item dt {
      font-size: var(--text-xs);
      color: var(--color-text-muted);
      text-transform: uppercase;
      letter-spacing: .05em;
      margin-bottom: var(--space-xs);
    }
    .edge-summary-item dd {
      font-size: var(--text-sm);
      font-weight: 600;
      color: var(--color-navy);
    }
  </style>
</head>
<body data-page="governed-edge-ai">

  <!-- NAV: copy verbatim from frameworks.html -->

  <main>

    <!-- HERO -->
    <section class="page-header">
      <div class="container">
        <span class="page-header__label" data-i18n="edge.hero.label">Etude de cas</span>
        <h1 class="page-header__title" data-i18n="edge.hero.title">Governed Physical AI</h1>
        <p class="page-header__sub" data-i18n="edge.hero.sub">Peut-on implementer les controles requis par l'AI Act sur du materiel embarque grand public ?</p>
      </div>
    </section>

    <!-- SUMMARY -->
    <section class="section section--light">
      <div class="container">
        <h2 data-i18n="edge.summary.h2">De quoi s'agit-il ?</h2>
        <p data-i18n="edge.summary.p1"></p>
        <p data-i18n="edge.summary.p2"></p>
        <dl class="edge-summary-grid">
          <div class="edge-summary-item">
            <dt data-i18n="edge.summary.cost">Cout du materiel</dt>
            <dd data-i18n="edge.summary.costval">Moins de 200 EUR</dd>
          </div>
          <div class="edge-summary-item">
            <dt data-i18n="edge.summary.boards">Cartes</dt>
            <dd data-i18n="edge.summary.boardsval">3 Arduino</dd>
          </div>
          <div class="edge-summary-item">
            <dt data-i18n="edge.summary.tests">Tests automatises</dt>
            <dd data-i18n="edge.summary.testsval">241 tests, 95.76% couverture</dd>
          </div>
        </dl>
      </div>
    </section>

    <!-- ARCHITECTURE -->
    <section class="section section--dark">
      <div class="container">
        <h2 data-i18n="edge.arch.h2">Architecture trois cartes</h2>
        <p data-i18n="edge.arch.p1"></p>
        <div class="edge-board-grid">
          <div class="edge-board-card">
            <h3 data-i18n="edge.arch.uno">Arduino UNO Q 4GB</h3>
            <p data-i18n="edge.arch.uno.role"></p>
          </div>
          <div class="edge-board-card">
            <h3 data-i18n="edge.arch.ventuno">Arduino VENTUNO Q</h3>
            <p data-i18n="edge.arch.ventuno.role"></p>
          </div>
          <div class="edge-board-card">
            <h3 data-i18n="edge.arch.alvik">Arduino Alvik</h3>
            <p data-i18n="edge.arch.alvik.role"></p>
          </div>
        </div>
        <p style="margin-top:var(--space-lg);font-size:var(--text-sm);color:var(--color-text-muted);">
          <strong data-i18n="edge.arch.proto">Protocole IPC</strong> :
          <span data-i18n="edge.arch.proto.val">Binaire CRC-16/CCITT sur UART serie, 8 types de message, max 261 octets</span>
        </p>
      </div>
    </section>

    <!-- BUILD STEPS -->
    <section class="section section--light">
      <div class="container">
        <h2 data-i18n="edge.steps.h2">Les 8 etapes de construction</h2>
        <p data-i18n="edge.steps.intro"></p>
        <ol class="edge-steps-list">
          <li>
            <div>
              <p class="edge-step-title" data-i18n="edge.step1.title">Etape 1 : Protocole IPC et codec binaire</p>
              <p data-i18n="edge.step1.body"></p>
            </div>
          </li>
          <li>
            <div>
              <p class="edge-step-title" data-i18n="edge.step2.title">Etape 2 : Journal d'audit SQLite</p>
              <p data-i18n="edge.step2.body"></p>
            </div>
          </li>
          <li>
            <div>
              <p class="edge-step-title" data-i18n="edge.step3.title">Etape 3 : Heartbeat et supervision</p>
              <p data-i18n="edge.step3.body"></p>
            </div>
          </li>
          <li>
            <div>
              <p class="edge-step-title" data-i18n="edge.step4.title">Etape 4 : Pair STM32H5 simule</p>
              <p data-i18n="edge.step4.body"></p>
            </div>
          </li>
          <li>
            <div>
              <p class="edge-step-title" data-i18n="edge.step5.title">Etape 5 : Interface de perception</p>
              <p data-i18n="edge.step5.body"></p>
            </div>
          </li>
          <li>
            <div>
              <p class="edge-step-title" data-i18n="edge.step6.title">Etape 6 : Filtre de gouvernance</p>
              <p data-i18n="edge.step6.body"></p>
            </div>
          </li>
          <li>
            <div>
              <p class="edge-step-title" data-i18n="edge.step7.title">Etape 7 : Firmware Alvik (MicroPython)</p>
              <p data-i18n="edge.step7.body"></p>
            </div>
          </li>
          <li>
            <div>
              <p class="edge-step-title" data-i18n="edge.step8.title">Etape 8 : Services UNO Q et VENTUNO Q</p>
              <p data-i18n="edge.step8.body"></p>
            </div>
          </li>
        </ol>
      </div>
    </section>

    <!-- GOVERNANCE CONTROLS -->
    <section class="section section--dark">
      <div class="container">
        <h2 data-i18n="edge.gov.h2">Controles de gouvernance implementes</h2>
        <p data-i18n="edge.gov.intro"></p>
        <ul class="edge-gov-list">
          <li data-i18n="edge.gov.g1"></li>
          <li data-i18n="edge.gov.g2"></li>
          <li data-i18n="edge.gov.g3"></li>
          <li data-i18n="edge.gov.g4"></li>
          <li data-i18n="edge.gov.g5"></li>
          <li data-i18n="edge.gov.g6"></li>
        </ul>
      </div>
    </section>

    <!-- QA BASELINE -->
    <section class="section section--light">
      <div class="container">
        <h2 data-i18n="edge.qa.h2">Qualite et reproductibilite</h2>
        <p data-i18n="edge.qa.p1"></p>
        <div class="edge-stats-row">
          <div class="edge-stat">
            <span class="edge-stat__value" data-i18n="edge.qa.tests">241 tests automatises</span>
          </div>
          <div class="edge-stat">
            <span class="edge-stat__value" data-i18n="edge.qa.cov">95.76%</span>
          </div>
          <div class="edge-stat">
            <span class="edge-stat__value" data-i18n="edge.qa.linters">Ruff / mypy / bandit</span>
          </div>
          <div class="edge-stat">
            <span class="edge-stat__value" data-i18n="edge.qa.ci">CI sans materiel</span>
          </div>
        </div>
      </div>
    </section>

    <!-- REPOSITORY -->
    <section class="section section--dark">
      <div class="container">
        <h2 data-i18n="edge.repo.h2">Depot et reutilisation</h2>
        <p data-i18n="edge.repo.p1"></p>
        <p>
          <a href="https://github.com/thierrysays/governed-edge-ai"
             target="_blank" rel="noopener"
             class="btn btn--teal" data-i18n="edge.repo.link">Voir le depot GitHub</a>
        </p>
        <p style="margin-top:var(--space-md);font-size:var(--text-sm);color:var(--color-text-muted);"
           data-i18n="edge.repo.note"></p>
      </div>
    </section>

  </main>

  <!-- FOOTER: copy verbatim from frameworks.html -->

  <script src="/assets/js/i18n.js?v=4"></script>
  <script src="/assets/js/main.js?v=4" defer></script>
</body>
</html>
```

---

## Checklist before publishing

- [ ] Fetch `https://www.glossolalie.pro/frameworks.html` to extract the exact nav and footer markup; paste verbatim into `governed-edge-ai.html`
- [ ] Add all `edge.*` i18n keys to both `fr` and `en` objects in `assets/js/i18n.js`; verify with `node --check assets/js/i18n.js`
- [ ] Confirm no `type="module"` in any `<script>` tag in this file
- [ ] Confirm no `&amp;`, `&gt;`, or similar HTML entities inside any `data-i18n` value
- [ ] Confirm no em-dashes anywhere (search for `—`)
- [ ] Bump CSS/JS version to `?v=5` if any JS or CSS file was modified; otherwise keep `?v=4`
- [ ] Add a nav link for the new page (check if the existing nav has a "Projets" or similar section where it should appear, or add it after the frameworks link)
- [ ] Run `php -l` on any PHP file if it was touched
- [ ] Build and deploy the ZIP: `cd public_html && find . -type f ! -path "./.git/*" | sort | zip /tmp/deploy.zip -@` then verify `unzip -l /tmp/deploy.zip | head` shows `.htaccess` at the root, not inside a subfolder

---

## Nav link addition

In the existing `site-nav` markup (in every HTML file that contains it), add a nav item for this new page. Check the nav structure first. The link text should be bilingual:

```html
<a href="/governed-edge-ai.html" data-i18n="nav.edge">Governed Physical AI</a>
```

Add to `i18n.js` in both language objects:

```js
'nav.edge': { fr: 'Governed Physical AI', en: 'Governed Physical AI' },
```

(The title is the same in both languages: it is a proper name / project title, not a translation.)
