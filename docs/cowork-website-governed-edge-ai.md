# Cowork Brief: governed-edge-ai.html

**Version 2.0, 19 August 2026.** Supersedes v1, which described a three-board rig with 241 tests. Almost every number and half the architecture in v1 is now wrong. **This revision is an update brief, not a create-from-scratch brief.**

**Target page**: `https://www.glossolalie.pro/governed-edge-ai.html`
**Task**: bring the existing page and its i18n keys in line with the project as it now stands.

---

## What changed since v1, in one table

Read this before touching anything. If the live page still says any of the left-hand column, it needs the right-hand one.

| Element | v1 said | Now |
|---|---|---|
| Boards | 3 Arduino | **5** Arduino: UNO Q 4GB, VENTUNO Q, Alvik, UNO R4 WiFi, Nesso N1 |
| Tests | 241, 95.76% coverage | **703**, **100%** line coverage on both modules, gate at 98% |
| Build steps | 8 | **11 shipped**, six more designed and scheduled |
| Physical stop | "hardware kill switch" on the robot | **Bistable relay contact in the robot's motor supply**, held by a board that is not on the command chain |
| Licence | MIT | **Apache 2.0** for code, CERN OHL-P v2 for hardware files, CC BY 4.0 for documentation |
| Perception | On the UNO Q, primary | On the UNO Q today; the UNO Q is being reclassified as an **independent witness** whose disagreement forces a HALT |
| Cameras | Unsourced | **Arducam IMX219 8 MP, two of them**, splayed for roughly 120° |
| Cost | Under EUR 200 | **Do not restate a figure.** Five boards plus a relay and two cameras is well past EUR 200, and the honest claim now is "commodity parts, no bespoke silicon", not a price. |

The old cost claim is the one to be most careful with: it was true of the three-board rig and repeating it now would be a false statement on a page whose entire argument is about not overclaiming.

---

## Context

Thierry Sayegh-Sauvage (Glossolalie Advisory, Paris) maintains **governed-edge-ai**, an open-source demonstrator showing that AI governance controls can be enforced in circuitry rather than described in policy. The repository is `https://github.com/thierrysays/governed-edge-ai` (public). The page is a project showcase on `glossolalie.pro`, not a blog article, and it should read as a senior practitioner's note to peers.

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

## The argument the page has to carry

One sentence: **governance controls that only exist in a document are not controls, and this rig is what it takes to make three of them real.**

Three, specifically, and the page is stronger if it names them as claims that can be checked rather than as features:

1. **Log before act.** No motor command is transmitted before its audit row exists. Enforced structurally: the audit reference is a return value, and the robot's own firmware rejects any command that does not carry one.
2. **Witness before act.** The audit chain head reaches a board the governance host does not control before the command frame is written. That is what makes the retained digests evidence rather than a log of a log.
3. **Enforcement that outlives its enforcer.** The stop is a bistable relay contact in the motor supply. It holds with no current at all, so cutting power to the oversight board does not restore motor power, and it needs no cooperation from the robot because the robot has no pin to read.

The third is new since v1 and it is the most quotable, because it came from finding a real fault: the previous design was a signal wire that released when its own board lost power. A safety control that stops enforcing when its board dies is not a safety control.

---

## Three things worth saying that most project pages do not

These are the credibility of the page. Keep them.

**The project found three real defects in itself, and says so.** A missing frame-length guard that let one hostile message wedge a link permanently. A failed transmission that left the audit log claiming a command had been sent. And the kill line failing open on power loss, which no test could have caught because the test doubles modelled logic and had no power to lose. Two were found by adversarial tests, one by writing the deployment instructions and asking what a reader would actually wire.

**The security tests assert what does not hold.** Anyone with physical access to the oversight cable can forge a message that releases the soft veto, and there is a test that does exactly that. The relay is unaffected, which is the reason there are two paths rather than one. A control whose limits are undocumented is a control nobody can rely on.

**The hardware layer is untested and the page should say so.** Pin timing, the LED matrix, serial throughput and the electrical behaviour of the relay all need the physical rig. Every timing figure in the protocol specification is a design target, not a measurement.

---

## i18n keys

Replace the values of the existing `edge.*` keys with these, and add the new ones. Same rules as v1: escaped straight apostrophes, no curly apostrophes, no HTML entities in `data-i18n`, no em-dashes.

```js
// ---- governed-edge-ai page, v2 ----
'edge.meta.title':       { fr: 'Governed Physical AI', en: 'Governed Physical AI' },
'edge.meta.desc':        { fr: 'Etude de cas : controles de gouvernance IA appliques dans le circuit, sur cinq cartes Arduino', en: 'Case study: AI governance controls enforced in circuitry, across five Arduino boards' },
'edge.hero.title':       { fr: 'Governed Physical AI', en: 'Governed Physical AI' },
'edge.hero.sub':         { fr: 'Un controle de gouvernance qui n\'existe que dans un document n\'est pas un controle. Voici ce qu\'il faut pour en rendre trois reels.', en: 'A governance control that exists only in a document is not a control. This is what it takes to make three of them real.' },
'edge.hero.label':       { fr: 'Etude de cas', en: 'Case study' },

'edge.summary.h2':       { fr: 'De quoi s\'agit-il ?', en: 'What is this?' },
'edge.summary.p1':       { fr: 'Cinq cartes Arduino, une tache chacune. Une observe le monde, une decide et journalise, une est le robot gouverne, une arbitre la securite depuis l\'exterieur de la chaine de commande, une sert de console hors bande. Aucune commande moteur n\'est transmise avant l\'ecriture de son entree d\'audit, et aucun logiciel ne peut retablir l\'alimentation moteur une fois que l\'arbitre a ouvert le contact.', en: 'Five Arduino boards, one job each. One watches the world, one decides and journals, one is the governed robot, one arbitrates safety from outside the command chain, one is an out-of-band console. No motor command is transmitted before its audit entry is written, and no software anywhere can restore motor power once the arbiter has opened the contact.' },
'edge.summary.p2':       { fr: 'Le code est open source sous Apache 2.0. La suite complete s\'execute sans materiel physique : les doublures sont de vraies implementations des machines d\'etat, pilotees par pseudo-terminaux, donc le chemin exercice en CI est celui qui tourne sur le banc.', en: 'The code is open source under Apache 2.0. The full suite runs with no physical hardware: the test doubles are real implementations of the state machines driven over pseudo-terminals, so the path exercised in CI is the one that runs on the rig.' },
'edge.summary.boards':   { fr: 'Cartes', en: 'Boards' },
'edge.summary.boardsval':{ fr: '5 Arduino, une tache chacune', en: '5 Arduino, one job each' },
'edge.summary.tests':    { fr: 'Tests automatises', en: 'Automated tests' },
'edge.summary.testsval': { fr: '703 tests, 100% de couverture de ligne', en: '703 tests, 100% line coverage' },
'edge.summary.enforce':  { fr: 'Application physique', en: 'Physical enforcement' },
'edge.summary.enforceval':{ fr: 'Relais bistable dans l\'alimentation moteur', en: 'Bistable relay in the motor supply' },

'edge.arch.h2':          { fr: 'Cinq cartes, une tache chacune', en: 'Five boards, one job each' },
'edge.arch.p1':          { fr: 'La regle qui organise le tout : aucune carte ne decide et n\'applique a la fois. Cela se verifie en regardant le cablage, pas en lisant une politique.', en: 'The rule that organises all of it: no board both decides and enforces. That is checkable by looking at the wiring, not by reading a policy.' },
'edge.arch.unoq':        { fr: 'Arduino UNO Q 4GB', en: 'Arduino UNO Q 4GB' },
'edge.arch.unoq.role':   { fr: 'Temoin : seconde observation, modele independant. Un desaccord force un HALT. Le temoin peut arreter, jamais demarrer.', en: 'Witness: a second observation from an independent model. Disagreement forces a HALT. The witness can stop, never start.' },
'edge.arch.ventuno':     { fr: 'Arduino VENTUNO Q', en: 'Arduino VENTUNO Q' },
'edge.arch.ventuno.role':{ fr: 'Chemin de decision, explicitement revocable : perception, filtre de gouvernance, journal d\'audit SQLite en chaine SHA-256.', en: 'The decision path, explicitly revocable: perception, governance filter, SQLite audit journal in a SHA-256 chain.' },
'edge.arch.alvik':       { fr: 'Arduino Alvik', en: 'Arduino Alvik' },
'edge.arch.alvik.role':  { fr: 'Corps gouverne : execute, et refuse toute commande sans reference d\'audit valide. Ses moteurs sont alimentes a travers le contact du relais.', en: 'The governed body: executes, and refuses any command without a valid audit reference. Its motors are powered through the relay contact.' },
'edge.arch.r4':          { fr: 'Arduino UNO R4 WiFi', en: 'Arduino UNO R4 WiFi' },
'edge.arch.r4.role':     { fr: 'Arbitre de securite, hors de la chaine de commande : bouton d\'arret, chien de garde, 64 empreintes d\'audit conservees hors hote, et le relais.', en: 'Safety arbiter, outside the command chain: stop button, watchdog, 64 audit digests retained off-host, and the relay.' },
'edge.arch.nesso':       { fr: 'Arduino Nesso N1', en: 'Arduino Nesso N1' },
'edge.arch.nesso.role':  { fr: 'Console hors bande : verdicts vers un operateur ailleurs, levee de HALT signee en retour. Concu, pas encore ecrit.', en: 'Out-of-band console: verdicts to an operator elsewhere, a signed HALT lift back. Designed, not yet written.' },
'edge.arch.least':       { fr: 'Pourquoi l\'arbitre est la carte la moins capable', en: 'Why the arbiter is the least capable board' },
'edge.arch.least.val':   { fr: 'Quelques centaines de lignes de C++, sans ordonnanceur, sans systeme de fichiers, sans pile reseau par defaut. Assez court pour etre lu d\'une traite, ce qui est exactement ce qu\'on attend d\'un superviseur.', en: 'A few hundred lines of C++ with no scheduler, no filesystem and no network stack by default. Short enough to read in one sitting, which is what a supervisor should be.' },

'edge.latch.h2':         { fr: 'L\'arret est un contact, pas un message', en: 'The stop is a contact, not a message' },
'edge.latch.p1':         { fr: 'La version precedente utilisait un fil de signal de l\'arbitre vers une broche du robot. Elle avait deux defauts. Elle lachait quand l\'arbitre perdait son alimentation, ce qui est la mauvaise direction pour un organe de securite. Et elle ne fonctionnait que parce que le firmware du robot choisissait de lire cette broche : un module de gouvernance accroche au composant gouverne.', en: 'The previous version used a signal wire from the arbiter into a pin on the robot. It had two faults. It released when the arbiter lost power, which is the wrong direction for a safety control. And it worked only because the robot\'s firmware chose to read that pin: a governance module bolted onto the governed component.' },
'edge.latch.p2':         { fr: 'Un contact bistable dans l\'alimentation moteur n\'a ni l\'un ni l\'autre. Il conserve sa position sans aucun courant, donc il survit a une coupure sur n\'importe quelle carte du banc, et il est dans l\'alimentation, donc le robot n\'a rien a accepter.', en: 'A bistable contact in the motor supply has neither fault. It holds position with no current at all, so it survives a power cut at any board in the rig, and it is in the supply, so the robot has nothing to agree to.' },
'edge.latch.p3':         { fr: 'Le contact est relu en permanence, et jamais suppose. Deux canaux optocouples cables en antivalence : un desaccord, une rupture de fil ou une batterie a plat donnent INCONNU, et rien n\'arrondit INCONNU en isolation. Un controle ne doit jamais revendiquer une securite qu\'il n\'a pas observee.', en: 'The contact is read back continuously, never assumed. Two opto-isolated channels wired antivalent: a disagreement, a broken wire or a flat battery all give UNKNOWN, and nothing rounds UNKNOWN up to isolation. A control must never claim a safety it has not observed.' },

'edge.gov.h2':           { fr: 'Controles de gouvernance implementes', en: 'Governance controls implemented' },
'edge.gov.intro':        { fr: 'Chacun est applique en code ou en cablage, et chacun a un test qui le nomme. Les references de cadre sont indicatives : aucun organisme n\'a certifie ce montage.', en: 'Each is enforced in code or in wiring, and each has a test that names it. The framework references are indicative: no body has certified this rig.' },
'edge.gov.g1':           { fr: 'Journaliser avant d\'agir : la reference d\'audit est obtenue avant toute transmission. Un echec de journalisation signifie aucune commande envoyee, structurellement.', en: 'Log before act: the audit reference is obtained before any transmission. A logging failure means no command sent, structurally.' },
'edge.gov.g2':           { fr: 'Temoigner avant d\'agir : la tete de chaine SHA-256 atteint une carte que l\'hote de gouvernance ne controle pas, avant l\'ecriture de la trame de commande.', en: 'Witness before act: the SHA-256 chain head reaches a board the governance host does not control, before the command frame is written.' },
'edge.gov.g3':           { fr: 'Double seuil de confiance : 0.70 en float64 cote Linux, 0.70 en float32 cote firmware, appliques independamment.', en: 'Dual confidence gate: 0.70 float64 on the Linux side, 0.70 float32 on the firmware side, independently enforced.' },
'edge.gov.g4':           { fr: 'Autorite humaine hors de la pile IA : bouton physique normalement ferme sur l\'arbitre. Il se verrouille, et aucun message du protocole ne le libere.', en: 'Human authority outside the AI stack: a physical normally closed button on the arbiter. It latches, and no protocol message releases it.' },
'edge.gov.g5':           { fr: 'Application qui survit a celui qui l\'applique : contact bistable dans l\'alimentation moteur, conserve sans courant a travers une coupure sur chaque carte.', en: 'Enforcement that outlives its enforcer: a bistable contact in the motor supply, held with no current through a power cut at every board.' },
'edge.gov.g6':           { fr: 'Preuve d\'alteration hors hote : 64 empreintes de chaine conservees sur une carte que l\'hote ne controle pas. Reecrire le journal se detecte a la reconciliation.', en: 'Off-host tamper evidence: 64 chain digests retained on a board the host does not control. Rewriting the journal is detected on reconciliation.' },
'edge.gov.g7':           { fr: 'Fermeture sur perte de supervision : le silence de l\'arbitre compte comme un veto. Un superviseur injoignable n\'est pas un superviseur satisfait.', en: 'Fail closed on loss of oversight: silence from the arbiter counts as a veto. A supervisor that cannot be reached is not a satisfied supervisor.' },

'edge.qa.h2':            { fr: 'Qualite, et ce qui n\'est pas teste', en: 'Quality, and what is not tested' },
'edge.qa.p1':            { fr: 'Tout s\'execute sans materiel physique. Les doublures sont de vraies implementations des machines d\'etat pilotees par pseudo-terminaux, pas des bouchons : le chemin exercice en CI est celui du banc.', en: 'Everything runs without physical hardware. The test doubles are real implementations of the state machines driven over pseudo-terminals, not stubs: the path exercised in CI is the one on the rig.' },
'edge.qa.tests':         { fr: '703 tests automatises', en: '703 automated tests' },
'edge.qa.cov':           { fr: '100% de couverture de ligne sur les deux modules', en: '100% line coverage on both modules' },
'edge.qa.linters':       { fr: 'Ruff, mypy strict, bandit SAST, pip-audit CVE, tous propres', en: 'Ruff, mypy strict, bandit SAST, pip-audit CVE, all clean' },
'edge.qa.parity':        { fr: 'Le firmware C++ est compile et confronte au modele Python de reference', en: 'The C++ firmware is compiled and checked against the Python reference model' },
'edge.qa.p2':            { fr: 'Trois defauts reels ont ete trouves ainsi plutot qu\'en relecture, dont un fil d\'arret qui lachait a la coupure d\'alimentation. Aucun test ne pouvait l\'attraper : les doublures modelisaient une logique, sans alimentation a perdre.', en: 'Three real defects were found this way rather than by review, including a kill line that released on power loss. No test could have caught it: the doubles modelled logic, with no power to lose.' },
'edge.qa.p3':            { fr: 'Ce qui n\'est pas teste : la couche materielle elle-meme. Chronometrage des broches, matrice LED, debit serie, comportement electrique du relais. Chaque chiffre de temporisation de la specification est une cible de conception, pas une mesure.', en: 'What is not tested: the hardware layer itself. Pin timing, the LED matrix, serial throughput, the electrical behaviour of the relay. Every timing figure in the specification is a design target, not a measurement.' },

'edge.limits.h2':        { fr: 'Ce que ce montage ne resout pas', en: 'What this rig does not solve' },
'edge.limits.l1':        { fr: 'La chaine d\'audit n\'est pas signee. Elle detecte l\'alteration de lignes deja temoignees, pas la falsification apres compromission de l\'hote. La signature est l\'increment suivant.', en: 'The audit chain is unkeyed. It detects tampering with rows already witnessed, not forgery after a host compromise. Signing is the next increment.' },
'edge.limits.l2':        { fr: 'Le lien de supervision vaut ce que vaut le cable. Qui peut y ecrire peut forger la levee du veto logiciel, et un test le demontre. Le contact du relais, lui, n\'est atteignable par aucun message.', en: 'The oversight link is worth exactly what the cable is worth. Anyone who can write to it can forge a release of the soft veto, and a test demonstrates it. The relay contact is reachable by no message at all.' },
'edge.limits.l3':        { fr: 'Le seuil de confiance de 0.70 est un jugement d\'ingenierie. Aucun standard publie ne relie un score de confiance a une probabilite de blessure.', en: 'The 0.70 confidence threshold is an engineering judgment. No published standard maps a confidence score to an injury probability.' },
'edge.limits.l4':        { fr: 'L\'acces physique au cablage moteur contourne tout. L\'application physique suppose la garde physique du materiel, et le dire vaut mieux que le sous-entendre.', en: 'Physical access to the motor wiring bypasses everything. Physical enforcement assumes physical custody of the rig, and saying so beats implying otherwise.' },

'edge.repo.h2':          { fr: 'Depot et reutilisation', en: 'Repository and reuse' },
'edge.repo.p1':          { fr: 'Code sous Apache 2.0, fichiers de conception materielle sous CERN OHL-P v2, documentation sous CC BY 4.0. Chaque couche (codec IPC, journal d\'audit, filtre de gouvernance, chaine d\'attestation, pilote de relais) est reutilisable independamment.', en: 'Code under Apache 2.0, hardware design files under CERN OHL-P v2, documentation under CC BY 4.0. Each layer (IPC codec, audit journal, governance filter, attestation chain, relay driver) is independently reusable.' },
'edge.repo.link':        { fr: 'Voir le depot GitHub', en: 'View GitHub repository' },
'edge.repo.note':        { fr: 'Specification complete dans docs/architecture.md, et un guide de deploiement pas a pas depuis zero dans docs/deployment-guide.md', en: 'Full specification in docs/architecture.md, and a step-by-step guide from bare metal in docs/deployment-guide.md' },
```

### Keys to remove

`edge.summary.cost` and `edge.summary.costval` go. The cost claim does not survive the five-board rig and there is no honest replacement figure. Remove the corresponding stat block from the HTML rather than leaving an empty tile.

`edge.step1` through `edge.step8`, `edge.steps.h2` and `edge.steps.intro` also go. The eight-step walkthrough was a build diary and it has been overtaken twice. Replace that whole section with the "The stop is a contact, not a message" section (`edge.latch.*`) and the limits section (`edge.limits.*`), both of which say more in less space and neither of which goes stale on the next build step.

---

## HTML changes

The page structure stays as it is. Three section-level edits:

1. **Architecture section**: five board cards instead of three. Keys `edge.arch.unoq`, `edge.arch.ventuno`, `edge.arch.alvik`, `edge.arch.r4`, `edge.arch.nesso` and their `.role` pairs, plus the `edge.arch.least` callout.
2. **Replace the build-steps section** with two new ones: the latch relay explanation (`edge.latch.*`, on a `section--light`) and the limits (`edge.limits.*`, on a `section--dark`). Limits last but before the repository section: ending on what the project does not do is the tone the rest of the site holds.
3. **Governance controls**: seven items now, `edge.gov.g1` to `edge.gov.g7`.

Everything else, nav, footer, head, script tags, stays exactly as the live page has it. Do not regenerate the file from scratch.

### Text-only diagram for the architecture section

If the architecture section carries a preformatted diagram, this is the current one. Keep it inside a `<pre>` and do not translate it: it is a wiring diagram, not prose.

```
                       Nesso N1
                       out-of-band console
                             |
UNO Q 4GB  ----------> VENTUNO Q --------------> Alvik
witness                decision path, revocable   governed body
independent model      audit journal              motors
disagreement                 ^                        ^
forces HALT     heartbeat +  |  reports only          | motor +V
                digests      |                        |
                        UNO R4 WiFi                   |
                        safety arbiter                |
                        64 retained digests           |
                             | Qwiic I2C              |
                             +--> Latch Relay --------+
                                  bistable contact
                                  + antivalent sense
```

## Checklist before publishing

- [ ] Fetch the live `governed-edge-ai.html` and edit it in place. Do not regenerate the file: the nav, footer and head are correct as they are.
- [ ] Update the `edge.*` values in both the `fr` and `en` objects in `assets/js/i18n.js`; remove `edge.summary.cost`, `edge.summary.costval` and the whole `edge.step1` to `edge.step8` block along with `edge.steps.h2` and `edge.steps.intro`; verify with `node --check assets/js/i18n.js`
- [ ] Confirm every key used in the HTML exists in both language objects, and that no removed key is still referenced
- [ ] Confirm no `type="module"` in any `<script>` tag in this file
- [ ] Confirm no `&amp;`, `&gt;`, or similar HTML entities inside any `data-i18n` value
- [ ] Confirm no em-dashes anywhere (search for the character itself)
- [ ] Confirm the page no longer states a hardware cost figure anywhere, including in meta description and og tags
- [ ] Search the whole site for the old figures and fix any that appear on other pages: "three boards", "trois cartes", "241", "95.76", "MIT"
- [ ] Bump CSS/JS version to the next `?v=` if `i18n.js` was modified, across every page that loads it
- [ ] Run `php -l` on any PHP file if it was touched
- [ ] Build and deploy the ZIP: `cd public_html && find . -type f ! -path "./.git/*" | sort | zip /tmp/deploy.zip -@` then verify `unzip -l /tmp/deploy.zip | head` shows `.htaccess` at the root, not inside a subfolder

---

## Nav

The nav entry already exists and does not change:

```html
<a href="/governed-edge-ai.html" data-i18n="nav.edge">Governed Physical AI</a>
```

The title is the same in both languages: it is a project name, not a translation.
