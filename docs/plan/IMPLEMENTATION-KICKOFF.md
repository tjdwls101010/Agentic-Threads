# Implementation kickoff prompt (paste into a fresh session)

Open a new Claude Code session **in the `Agentic Threads` repo** and paste the box below.

---

```
agentic-threads v0.1 (Threads 리더)를 구현해줘. 이건 구현 세션이야 (계획은 지난 세션에
끝났고, 계획과 구현을 분리하기 위한 것).

먼저 계획 문서를 순서대로 정독해 (docs/plan/):
- 00-overview.md            ← 목적/비목적, "Threads ≈ Facebook" 핵심
- 01-decisions.md           ← 사용자와 합의된 결정 로그 (D1~D13) + Phase 0 오픈 질문
- 02-recon-findings.md      ← 라이브 리콘 실측 (엔드포인트·인증·doc_id·envelope·페이지네이션)
- 03-architecture.md        ← 모듈 구조, 네이밍, 저장소, 데이터 모델/스키마
- 04-cli-spec.md            ← 전체 명령 표면, 플래그, exit code, 출력 계약
- 05-testing-and-ci.md      ← 테스트/픽스처/CI/배포/PII 규율
- 06-implementation-phases.md ← verify 게이트가 달린 단계별 로드맵 (메인)
- 07-skill-plan.md          ← 스킬은 PyPI 배포 후 별도 세션 (지금 X)

배경 한 줄: agentic-threads는 형제 도구 agentic-facebook / agentic-x(agentic-twitter)의
셋째다. 리콘으로 Threads가 Meta GraphQL(doc_id + fb_dtsg/lsd + sessionid)임이 증명됐고 —
즉 "와이어 포맷은 Facebook, 형태는 X". X의 transaction-id 벽은 없다. 그래서 셋 중 가장 쉽다.

반드시 지킬 제약 (CLAUDE.md + 형제 규율):
- 최소 코드, 수술적 변경, 투기적 추상화·미요청 기능 금지. D2 스코프 밖(미디어/리포스트 탭,
  커뮤니티, 알림, 인사이트, 쓰기 작업, 무로그인 읽기)은 만들지 마.
- 전송: X형 — 읽기는 순수 httpx, scrapling은 [browser] 익스트라로 로그인/수확에서만 lazy
  import. transaction.py / observe.py / GATED_OPS / static bearer 트릭은 절대 포팅하지 마.
- rate floor 1.0s(non-bypassable), 단일 타깃·무 배치·무 데몬·무 crawl.
- PII: scratch/, *.raw.json, output/, profiles/ gitignore. 픽스처는 합성·PII 스캔 통과.
  실제 캡처 커밋 금지.
- test_no_scrapling_import.py 그린 유지(베이스 임포트에 scrapling 금지).
- 로그인은 headed 스텔스 브라우저 + 폴링 감지(input() 금지 — 에이전트 데드락). + --cookies import.
- DISCLAIMER 톤 약화 금지. 버려도 되는 Instagram 계정만.
- 산출물은 전부 영어(코드/주석/README/CLI 출력).

진행 방식 (각 Phase의 verify 게이트를 통과할 때까지 loop):
0) Phase 0 재검증 필수 — doc_id는 회전하니 패키지 자체 scrapling(StealthySession,
   capture_xhr=r"/graphql/")로 재수확. 오픈 질문 Q-A~Q-E(특히: 읽기에 fb_dtsg가 필요한가?
   shortcode→postID 변환? following op 이름 + envelope leaf 경로?)를 라이브로 확정하고
   02-recon-findings.md를 갱신해. recon 스크립트는 gitignore된 scratch/에.
1) 스캐폴드 + 패키징 + 오프라인 명령(catalog/schema) + CI/publish.yml/pre-commit.
2) 인증 + 로그인(폴링) + 세션 + tokens(Q-A면).
3) 읽기 클라이언트 + GraphQL + 파서 — 수직 슬라이스 하나(`fetch`)를 라이브로 관통.
4) 나머지 프리미티브(feed/post/search/followers/following, fetch --replies).
5) 하드닝 + soft-lock + doctor --refresh + README/wiki/CHANGELOG/DISCLAIMER + 버전.
6) PR → main 머지 → GitHub Release(→ publish.yml → PyPI Trusted Publishing). 설치 검증.
7) 스킬은 별도 세션(07-skill-plan.md).

시작 전에: 계획 문서를 읽고 → Phase 0 실행 계획을 짧게 제시하고 진행. 계획을 벗어나는
스코프 변경이 필요하면 먼저 물어봐.
```

---

**Notes for you (not part of the paste):**

- Repo remote is `github.com/tjdwls101010/Agentic-Threads` (auto-publishes to PyPI via the
  configured pending publisher: workflow **`publish.yml`**, environment **(all)** — keep
  that filename). Plan docs are committed on `main` under `docs/plan/`.
- **Naming triple**: dist `agentic-threads` / import `agentic_threads` / command
  `agentic-threads`. Env override `AGENTIC_THREADS_PROFILE_DIR`.
- Library docs are cached locally at `../.tmp/docs_scrapling/` and `../.tmp/docs_crawl4ai/`
  (a Korean scrapling README is at `docs_scrapling/README_KR.md`). Only scrapling is a
  dependency here; crawl4ai is not used by this project.
- `init_script` for scrapling `StealthySession` must be an **absolute** path (validated).
- If the repo venv lacks the package / has a stale console-script shebang, run as
  `PYTHONPATH=src .venv/bin/python -m agentic_threads.cli …` and `git commit --no-verify`
  if the pre-commit hook can't launch (same workarounds as the FB/X siblings).
- Recon was done from throwaway account `@tjdwls101010` (ds_user_id `63485801431`); the
  captured `doc_id`s in `02-recon-findings.md` are a 2026-07-23 snapshot — re-anchor them.
