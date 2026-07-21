# Sisyphus-Harness 엄격 코드베이스 리뷰 및 95점 개선 계획

- 기준 리비전: `26e141d360dceddc984ca506ec5ed0daaccde6d4`
- 작성일: `2026-07-21`
- 평가 범위: README, architecture/security 문서, CI, 최신 main source, PR #1/#7 metadata와 review findings
- 검증 한계: 본 문서는 저장소의 최신 source와 CI/evidence 기록을 정적 검토한 결과이며, 외부 모델·Docker·전체 test suite를 본 작성 환경에서 독립 재실행한 보고서는 아니다.

구조화된 실행 문서:

- [`reviews/2026-07-21/README.md`](reviews/2026-07-21/README.md)
- [`reviews/2026-07-21/findings.md`](reviews/2026-07-21/findings.md)
- [`reviews/2026-07-21/remediation-roadmap.md`](reviews/2026-07-21/remediation-roadmap.md)
- [`reviews/2026-07-21/verification-gates.md`](reviews/2026-07-21/verification-gates.md)

> **엄격한 종합 점수: 43 / 100**
>
> **Production release 판정: BLOCKED**
> 연구·감독형 로컬 실험으로는 의미가 있으나, authoritative control plane과 독립 verifier로 설명하기에는 핵심 trust chain이 완성되지 않았다.

## 1. 핵심 결론

Sisyphus-Harness는 원본보다 범위가 집중됐고, SQLite transaction, bounded file tools, atomic writes, evidence receipt, Docker containment 같은 유의미한 재료를 확보했다. 그러나 현재 가장 정교한 구성요소인 EvidenceContract와 Docker verifier가 실제 최종 성공 상태를 지배하지 못한다. 기본 runtime에서 Agent는 verifier input을 변경할 수 있고, Worker는 AgentResult.success를 근거로 queue 완료를 기록하며, 공개 Qwen evidence는 최신 main이 아니라 초기 구현 revision을 증명한다.

따라서 지금 필요한 작업은 GraphRAG·evolution·evidence 표현식 확장이 아니다. **verifier oracle 분리 → 코드 위치/공통 코드 정리 → authoritative Control transition**의 순서로 신뢰 사슬을 다시 세워야 한다.

## 2. 점수표

| 영역 | 가중치 | 점수 | 판정 |
| --- | --- | --- | --- |
| 문제 정의와 범위 명시 | 8% | 8.0/10 | 실험 범위와 한계를 비교적 정직하게 기술 |
| 실제 아키텍처 경계 | 15% | 4.0/10 | 폴더는 분리됐지만 CLI·root module 결합이 큼 |
| 성공 판정·Authority 정확성 | 18% | 2.5/10 | Control 판정이 shadow-only이며 queue 완료를 지배하지 않음 |
| 보안 경계 | 15% | 3.5/10 | bounded tools는 강점이나 기본 verifier와 oracle 독립성이 취약 |
| 코드 구조·유지보수성 | 12% | 4.0/10 | 대형 module, 수동 validator, 중복 primitive |
| Persistence·Queue | 8% | 6.0/10 | SQLite transaction은 양호하나 job semantics는 미완성 |
| 테스트 공학 | 10% | 6.5/10 | 높은 coverage, 그러나 mock-heavy·type gate 부재 |
| Benchmark·실험 타당성 | 6% | 2.5/10 | train 4, holdout 2로 일반화 주장 불가 |
| CI·패키징 | 5% | 5.5/10 | 기본 gate는 있으나 플랫폼·supply chain 부족 |
| 거버넌스·성숙도 | 3% | 1.5/10 | 수만 줄 PR이 수 분 내 merge |

## 3. 유지할 가치가 있는 부분

- SQLite WAL, busy timeout, foreign-key 활성화와 BEGIN IMMEDIATE transaction을 사용한다.
- 모델 도구 표면을 6개 파일 도구로 제한하고 stale-content hash와 atomic replace를 사용한다.
- verification receipt를 artifact에서 다시 읽고 digest binding을 확인하려는 방향이 명확하다.
- queue attempt workspace를 source repository와 분리하는 구조가 도입됐다.
- branch coverage 90% gate, Ruff, Bandit, package build 및 wheel smoke test가 CI에 있다.
- Security 문서가 in-process verifier와 Docker transport의 한계를 비교적 솔직하게 명시한다.

이 장점들은 전체 시스템이 안전하다는 증명이 아니라, 재설계 시 보존할 수 있는 구현 자산이다.

## 4. P0 Critical Findings

### SH-P0-001 — Agent가 기본 runtime의 verifier input을 수정할 수 있다

**판정:** 검증 독립성이 성립하지 않는다. 구현을 고치는 대신 시험을 약화해 PASS를 만들 수 있다.

**근거**
- 직접 실행과 queue worker composition에서 protected_write_paths로 보호되는 것은 config_path뿐이다. 근거: src/sisyphus_harness/cli.py:304-324, worker.py:284-303.
- 예제 verification command는 candidate repository에서 python3 -m unittest discover를 실행한다. 근거: sisyphus-harness.example.toml:37-43.
- BoundedVerifier는 verification 호출 시점의 workspace를 baseline으로 잡는다. 검증 이전에 변경된 테스트는 candidate state로 간주된다. 근거: verifier.py:85-139.
- 최초 PR 리뷰에서도 동일 문제가 P1로 지적됐지만 최신 main composition에 구조적 해결이 반영되지 않았다. 근거: PR #1 review discussion.

**공격 또는 실패 경로**
1. Agent가 구현 코드 대신 tests 또는 fixture를 수정·삭제한다.
2. finish를 요청한다.
3. Verifier가 이미 변조된 workspace에서 unittest를 실행한다.
4. 검증 실행 중 추가 mutation이 없고 exit code가 0이면 passing receipt가 생성된다.

**영향**
- operator-controlled verification이라는 핵심 신뢰 주장이 무너진다.
- Agent와 oracle이 실질적으로 동일 변경 권한을 공유한다.
- 일반 direct/queue run의 성공 receipt를 독립 증거로 사용할 수 없다.

**개선 요구사항**
- candidate workspace와 immutable verifier bundle을 분리한다.
- receipt에 candidate bundle, verifier bundle, profile, command, image digest를 모두 bind한다.
- untrusted mode에서는 Docker 또는 동등한 namespace verifier를 기본값으로 한다.
- test, fixture, snapshot, imported helper를 verifier-owned read-only asset으로 제공한다.

**완료 조건**
- [ ] Agent가 tests assertion을 약화해도 final outcome이 FAIL이다.
- [ ] Agent가 verifier script, fixture, snapshot을 삭제하거나 shadow import해도 FAIL이다.
- [ ] receipt에서 verifier bundle digest와 candidate bundle digest를 독립 확인할 수 있다.
- [ ] default untrusted composition에 candidate-writable oracle path가 없다.

### SH-P0-002 — Control evidence 판정이 authoritative completion을 결정하지 않는다

**판정:** 가장 정교한 EvidenceContract가 실제 queue의 최종 진실을 지배하지 않는다.

**근거**
- ControlEvidenceContractService는 스스로 shadow adjudication이며 queue/task repository와 transition method가 없다고 명시한다. 근거: services/evidence_contract.py:27-34.
- Worker는 CodingJobResult.success가 true이면 queue.complete, false이면 queue.fail을 호출한다. 근거: worker.py:204-250.
- CodingJobResult.success는 AgentResult.success와 반드시 동일하도록 강제된다. 근거: contracts/control.py:23-51.
- 최신 자체 review도 queue COMPLETED는 execution-terminal compatibility state이며 semantic TaskOutcome은 미구현이라고 인정한다.

**영향**
- 독립 Control 판정이 FAIL 또는 INDETERMINATE여도 이미 기록된 queue COMPLETED를 무효화하지 못한다.
- 문서상의 authority flow와 실제 state transition이 불일치한다.
- stale attempt, 잘못된 contract, 잘못된 bundle의 결합을 최종 outcome 레이어에서 방지할 수 없다.

**개선 요구사항**
- execution terminal과 semantic TaskOutcome을 분리한다.
- Worker는 AttemptFinished만 발행하고 ACCEPTED/REJECTED를 기록하지 못하게 한다.
- Control이 authoritative store에서 task/spec/dispatch/job/attempt/bundle/contract digest를 다시 조회하고 fencing한 뒤 outcome을 발행한다.
- AgentResult.success를 agent_reported_success로 변경해 diagnostic field로 제한한다.

**완료 조건**
- [ ] Worker code path에는 queue.complete 또는 semantic success transition이 없다.
- [ ] Control evaluation PASS만 ACCEPTED를 생성할 수 있다.
- [ ] FAIL과 INDETERMINATE는 success로 변환되지 않는다.
- [ ] stale lease token 또는 stale attempt digest의 outcome 발행이 transaction에서 거부된다.

### SH-P0-003 — 공개된 Qwen evidence가 최신 main을 증명하지 않는다

**판정:** 증거의 무결성은 확인하지만 현재 코드와의 관련성은 확인하지 않는다.

**근거**
- evidence manifest의 source_revision은 최초 구현 merge commit 47539e0d...로 고정돼 있다.
- 초기 final verification은 139 tests, 2,556 statements를 기준으로 한다.
- 최신 자체 review는 356 tests, 6,637 statements와 Pyright 120 errors를 기록한다.
- verify_manifest.py는 과거 source_revision의 file hash를 git show로 검증하지만 current HEAD와 동일한지 검사하지 않는다.

**영향**
- README의 final-code evidence가 최신 코드의 성능과 안전성을 보증하는 것처럼 오해될 수 있다.
- CI green이 current-code evidence validation으로 잘못 해석된다.
- 보안·성능 regression이 있어도 historical artifact integrity check는 계속 통과한다.

**개선 요구사항**
- evidence에 current, release-bound, historical, stale 상태를 명시한다.
- release evidence는 target tree SHA와 exact package artifact digest에 bind한다.
- README의 Qwen 결과에 target revision과 n을 명시하고 최신 main과 다른 경우 historical로 표시한다.
- CI에서 current release evidence가 없으면 current claims를 publish하지 못하게 한다.

**완료 조건**
- [ ] manifest target revision과 current release revision이 다르면 CI가 stale로 판정한다.
- [ ] README의 모든 측정치에 revision, dataset size, runtime provenance가 표시된다.
- [ ] 현재 release wheel을 설치한 환경에서 direct·queue·benchmark evidence를 재생성한다.

### SH-P0-004 — 거대한 PR을 수 분 내 merge하는 개발 프로세스

**판정:** 코드 품질보다 merge 속도가 우선이며 meaningful review가 구조적으로 불가능하다.

**근거**
- PR #1은 153 files, +16,373 lines, 1 commit이었고 생성 후 약 53초 내 merge됐다.
- PR #7은 79 files, +18,891/-285 lines, 1 commit이었고 약 2분 내 merge됐다.
- PR #7의 자동 review는 merge 뒤 완료됐고 Docker mount path comma 결함을 발견했다.
- 현재 docker_verifier.py는 --mount src 값을 comma-delimited 문자열에 직접 삽입한다.

**영향**
- CI가 커버하지 않는 correctness·security 결함이 즉시 main에 유입된다.
- 변경 목적, migration, rollback, architecture decision을 독립적으로 검토할 수 없다.
- 사후 review 문서가 사전 quality gate를 대체하는 잘못된 문화가 형성된다.

**개선 요구사항**
- branch protection, required human approval, required automated review를 merge gate로 설정한다.
- authority/verifier/persistence 경로에 CODEOWNERS와 2인 review를 요구한다.
- 일반 code PR은 review 가능한 logical slice로 제한하고 evidence bundle은 별도 PR로 분리한다.
- merge queue에서 CI와 review가 모두 최신 head SHA를 검증하도록 한다.

**완료 조건**
- [ ] review 미완료 또는 stale approval 상태에서는 main merge가 불가능하다.
- [ ] security-sensitive path 변경에는 지정 reviewer 승인이 필요하다.
- [ ] 대규모 작업은 contract, implementation, migration, evidence PR로 분리된다.

### SH-P0-005 — Strict structured-output schema가 실제 provider에서 거부될 수 있다

**판정:** mock test는 request 생성만 검증하며 provider 호환성을 검증하지 않는다.

**근거**
- AGENT_DECISION_RESPONSE_FORMAT은 strict=true이면서 schema root가 object가 아닌 oneOf다. 근거: protocol.py:132-155.
- OpenAICompatibleProvider는 json_mode이면 해당 schema를 모든 요청에 보낸다. 근거: provider.py:70-88.
- provider test는 urlopen을 mock하고 schema가 payload에 포함됐는지만 확인한다. 근거: tests/test_provider.py:43-69.
- 최초 PR review에서도 strict endpoint의 400 가능성이 지적됐다.

**영향**
- 실제 strict validator를 쓰는 endpoint에서 agent가 시작 전에 실패할 수 있다.
- OpenAI-compatible이라는 표현이 실제 지원 범위를 과장한다.
- provider별 schema capability 차이를 runtime에서 처리할 수 없다.

**개선 요구사항**
- root object와 explicit discriminator를 사용한 schema로 변경한다.
- provider capability negotiation과 strict/non-strict fallback 정책을 도입한다.
- llama.cpp, strict OpenAI-compatible endpoint, schema 미지원 endpoint에 대한 contract test를 추가한다.

**완료 조건**
- [ ] 실제 strict schema validator가 response_format을 수락한다.
- [ ] schema 미지원 provider는 명시적 fallback 또는 actionable error를 반환한다.
- [ ] 지원 provider matrix가 README와 CI에 고정된다.

### SH-P0-006 — 기본 in-process verifier가 model-edited code를 operator 권한으로 실행한다

**판정:** bounded verifier는 process sandbox가 아니며 untrusted repository의 기본 경로로 부적합하다.

**근거**
- Verifier environment는 대부분의 os.environ을 상속하고 candidate workspace를 cwd로 사용한다. 근거: verifier.py:181-214.
- Security 문서는 candidate code가 verifier에서 실행되면 filesystem, environment, network 권한을 갖는다고 인정한다.
- Docker transport는 제공되지만 기본 direct/queue composition은 in-process verifier다.

**공격 또는 실패 경로**
1. Agent가 검증 과정에서 import되는 source에 credential read 또는 외부 write 코드를 삽입한다.
2. Verifier command가 해당 module을 import한다.
3. repository mutation 여부와 무관한 외부 filesystem/network side effect가 발생한다.

**영향**
- repository 밖 write, secret access, network exfiltration을 막지 못한다.
- workspace mutation detection은 외부 side effect를 예방하거나 되돌리지 못한다.
- untrusted model/repository에 대한 production 사용을 허용할 수 없다.

**개선 요구사항**
- trust mode를 trusted-in-process와 untrusted-contained로 명시적으로 분리한다.
- untrusted mode의 기본 verifier를 Docker/cgroup/PID namespace로 설정한다.
- environment allowlist, network deny, read-only verifier assets, image digest pin을 적용한다.

**완료 조건**
- [ ] untrusted default path에서 candidate code가 host home, environment secret, network에 접근할 수 없다.
- [ ] in-process 경로는 explicit trusted flag 없이는 실행되지 않는다.
- [ ] security tests가 외부 write와 credential access 시도를 차단한다.

## 5. P1 Structural and Operational Findings

| ID | 문제 | 필수 개선 |
| --- | --- | --- |
| SH-P1-001 | CLI가 parser·composition root·application service를 동시에 담당 | cli.py를 command module과 application use case로 분리 |
| SH-P1-002 | verification 개념이 contracts/ports/services/root/adapters/infra에 중복 분산 | domain/application/port/adapter/store의 단일 책임 naming으로 재배치 |
| SH-P1-003 | contracts/__init__.py가 수십 타입을 재-export하는 god namespace | subdomain 직접 import로 전환하고 public API를 최소화 |
| SH-P1-004 | architecture test가 일부 direct import만 검사 | 전체 import graph, transitive dependency, cycle, re-export 우회 검사 |
| SH-P1-005 | atomic write, SHA-256, ID validation, clock helper 중복 | shared/atomic_io.py, digests.py, identifiers.py, clocks.py로 통합 |
| SH-P1-006 | Pyright 120 errors가 informational이며 CI gate가 아님 | strict type error 0을 만든 뒤 blocking gate로 전환 |
| SH-P1-007 | agent.py, verifier.py, evidence evaluator, contracts가 과대형 | parser, invariant, lifecycle, renderer를 작은 pure function/module로 분리 |
| SH-P1-008 | queue state가 queued/running/completed/failed뿐이며 attempt·outcome 분리 없음 | Attempt, ExecutionTerminal, TaskOutcome 저장소 분리 |
| SH-P1-009 | lease가 wall clock을 사용하고 lost lease가 side effect를 즉시 중단하지 못함 | fencing token, cancellation channel, bounded retry/dead-letter 도입 |
| SH-P1-010 | Docker transport의 path comma 결함과 incomplete environment attestation | mount API 안전화, image/base digest 및 environment allowlist receipt binding |
| SH-P1-011 | artifact retention·redaction·quota·GC 없음 | retention policy, size budget, prune command, secret redaction 도입 |
| SH-P1-012 | Ubuntu Python 3.11/3.14 중심 CI | 3.11-3.14, Windows, macOS, filesystem/process semantics matrix |

## 6. P2 Product, Benchmark, and Evidence Findings

| ID | 문제 | 필수 개선 |
| --- | --- | --- |
| SH-P2-001 | Train 4개, holdout 2개인데 100% success를 전면 노출 | n, 분산, 신뢰구간을 표시하고 smoke benchmark로 명시 |
| SH-P2-002 | GEPA 내부 dataset과 valset이 동일 trainset | 내부 validation split과 외부 holdout을 독립 구성 |
| SH-P2-003 | case당 단일 rollout, 반복 seed와 variance 없음 | 반복 실행·비용·latency·failure distribution 측정 |
| SH-P2-004 | Agent/provider test가 mock-heavy | 실제 HTTP/provider/container integration 및 failure injection 확대 |
| SH-P2-005 | GitHub Action tag, base image tag 등 mutable supply-chain reference | commit SHA와 image digest pin, SBOM, provenance attestation |
| SH-P2-006 | 문서와 evidence 규모가 runtime authority 완성도보다 앞섬 | current/target/historical claim taxonomy와 release claim gate 도입 |

## 7. 근본 원인

- Trust chain이 완성되기 전에 GraphRAG, Evidence DSL, GEPA, 대규모 evidence 문서가 확장됐다.
- 보안 관련 용어와 경계가 많아졌지만 final authority의 소유자가 코드로 완결되지 않았다.
- 수동 wire-model validator를 반복 구현해 type debt와 module size가 급증했다.
- 기능 구현과 evidence 생성이 거대한 단일 PR에 묶여 review가 형식화됐다.
- 높은 coverage가 integration realism, race safety, current evidence relevance를 대신하고 있다.

## 8. 목표 아키텍처

```text
Operator TaskSpec + Verification Contract
                |
                v
        Dispatch / Attempt Store
                |
                v
Worker -> Candidate Output Bundle -> AttemptFinished
                |
                v
Control -> Immutable Verifier Bundle -> Receipt Re-read
                |
                v
EvidenceContract Evaluation -> TaskOutcome
                |
                v
       ACCEPTED / REJECTED / INDETERMINATE
```

### 권장 코드 구조

```text
src/sisyphus_harness/
  domain/
  application/
  ports/
  adapters/
  interfaces/cli/
  persistence/
  shared/
```

`shared/`에는 atomic I/O, digest, identifier, clock, strict JSON, path primitive만 둔다. Task outcome, lifecycle, verifier policy 같은 business rule은 shared로 이동시키지 않는다.

## 9. 순차 구현 로드맵

| 단계 | 목표 | 핵심 범위 | Exit Gate |
| --- | --- | --- | --- |
| Stage 0 | 기능 동결·긴급 수정 | SH-P0-001/003/004/005/006, Docker mount bug | Known exploit path 0, stale claim 차단 |
| Stage 1 | 코드 위치·공통 코드 정리 | shared primitive, CLI·agent·worker·verifier 분해 | 중복 primitive 0, import graph cycle 0 |
| Stage 2 | Authority chain 완성 | AttemptFinished → Control adjudication → TaskOutcome | Worker semantic completion 권한 0 |
| Stage 3 | Verifier 독립성 | immutable oracle bundle, contained default, full digest binding | oracle tampering·host escape tests PASS |
| Stage 4 | Type·Test hardening | Pyright 0, fuzz/property/mutation/concurrency tests | typing·race·crash gate blocking |
| Stage 5 | Benchmark 재설계 | 다양한 task, 반복 rollout, 독립 holdout/evaluator | 통계적으로 해석 가능한 결과 |
| Stage 6 | 거버넌스·Release | review gate, CODEOWNERS, supply chain, retention | 95점 release rubric 충족 |

## 10. Stage 1 상세: 코드 위치와 공통 코드 정리

- [ ] `receipts.py`와 `tools.py`의 atomic write/fsync 구현을 `shared/atomic_io.py`로 통합한다.
- [ ] CLI, Worker, Verifier의 file hash 구현을 `shared/digests.py`로 통합한다.
- [ ] run/evolution/verification/job ID regex를 `shared/identifiers.py`의 named policy로 통합한다.
- [ ] UTC timestamp와 lease clock을 분리해 `shared/clocks.py`에 wall/monotonic abstraction을 둔다.
- [ ] `cli.py`를 parser, command handler, application service로 분리한다.
- [ ] `agent.py`에서 message rendering, stagnation detection, verification transition, artifact writing을 분리한다.
- [ ] `worker.py`에서 payload codec, lease keeper, attempt executor, repository initializer를 분리한다.
- [ ] `verifier.py`에서 process lifecycle, output capture, receipt construction, evidence storage를 분리한다.
- [ ] `contracts/__init__.py` 대량 re-export와 `models.py` legacy alias를 축소한다.
- [ ] 전체 import graph test를 추가해 transitive cycle과 `__init__` 우회를 차단한다.

## 11. 95점 Release Gate

- [ ] P0 open finding 0개
- [ ] Worker의 semantic completion 권한 0개
- [ ] candidate-writable verifier oracle 0개
- [ ] Pyright strict error 0개
- [ ] current release evidence와 release artifact digest 일치
- [ ] fuzz/property/mutation/concurrency/crash test가 blocking CI에 포함
- [ ] Windows/macOS/Linux와 Python 3.11-3.14 검증
- [ ] human review, CODEOWNERS, branch protection 적용
- [ ] benchmark에 충분한 n, 반복 rollout, variance/confidence interval 표시
- [ ] SBOM, action SHA pin, image digest pin, retention/redaction policy 존재

## 12. 허용 가능한 주장과 금지할 주장

| 판정 | 주장 | 근거 |
| --- | --- | --- |
| 허용 | supervised single-user local experiment | README의 현재 experimental 범위와 일치 |
| 허용 | bounded direct file-tool coding loop | 6개 도구, path/hash/atomic write 경계 존재 |
| 조건부 | Docker host containment | hidden oracle 또는 multi-tenant confidentiality는 아님 |
| 금지 | production-grade secure control plane | authoritative Control outcome과 immutable oracle 미완성 |
| 금지 | latest main에서 검증된 Qwen 성능 | 공개 evidence가 이전 revision에 bind |
| 금지 | generalized benchmark improvement | holdout n=2, 반복·통계 없음 |

## 13. 최종 권고

현재 버전은 supervised single-user local experiment로 유지할 수 있다. Production, unattended operation, untrusted repository, multi-tenant, authoritative task control plane으로 승격하는 것은 차단해야 한다. 다음 구현 순서는 변경하면 안 된다.

1. verifier oracle 분리와 current evidence 정정
2. 코드 위치·중복 primitive 정리
3. Control-issued authoritative TaskOutcome
4. contained verifier 기본화
5. typing·fuzz·concurrency hardening
6. benchmark와 release governance 재설계

기능을 더 추가하는 것은 점수를 올리지 않는다. 가장 단순한 trust chain을 완성하고, 그 경계를 작은 코드와 강제 가능한 CI로 고정해야 95점에 접근할 수 있다.

## Appendix A. 주요 근거 인덱스

- `README.md:13-57` — experimental scope, in-process/Docker security limits
- `src/sisyphus_harness/cli.py:304-324` — direct run composition과 config-only protection
- `src/sisyphus_harness/worker.py:204-250, 284-315` — queue completion과 AgentResult 기반 success
- `src/sisyphus_harness/contracts/control.py:23-51` — CodingJobResult.success와 AgentResult.success 결합
- `src/sisyphus_harness/services/evidence_contract.py:27-34` — shadow adjudication 선언
- `src/sisyphus_harness/verifier.py:85-139, 181-214` — verification baseline과 operator environment execution
- `src/sisyphus_harness/protocol.py:132-155` — strict oneOf root response schema
- `evidence/qwen30b-final/manifest.json:3-10` — historical source revision binding
- `evidence/qwen30b-final/verify_manifest.py:24-64` — historical hash integrity validation
- `docs/code-review-2026-07-21.md` — open P1/P2 risks, 356 tests, 91.11% coverage, Pyright errors
- `PR #1` — 153 files, +16,373, 1 commit, 약 53초 내 merge
- `PR #7` — 79 files, +18,891/-285, 1 commit, 약 2분 내 merge
