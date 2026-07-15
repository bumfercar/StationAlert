# NextStop STT

NextStop STT는 지하철 안내방송을 RTZR Streaming STT로 인식하고, 목적지 도착 알림이
정확한지 평가하는 Python 프로젝트입니다.

단순히 역 이름이 전사됐는지만 확인하지 않습니다. STT 결과와 알림 판단을 분리하여
다음 세 가지를 함께 측정하는 것이 핵심입니다.

| 평가 대상 | 확인할 질문 | 지표 |
| --- | --- | --- |
| 음성인식 | 안내방송과 역명을 정확히 전사했는가? | CER, 역명 CER, 역명 일치율 |
| 알림 판단 | 필요한 순간에만 알림을 보냈는가? | Precision, Recall, F1, 오탐·미탐 수 |
| 실시간성 | 실제 하차 준비에 사용할 만큼 빨랐는가? | 최종 전사 및 전체 알림 지연시간 |

> **현재 상태:** Python 3.11 실행 환경, CLI, 테스트, 보안 기본 설정까지 구현했습니다.
> RTZR 인증, Streaming 전송, 목적지 판단, 실제 측정 결과는 아직 구현되지 않았으며
> 문서에서도 계획과 구현을 구분해서 표시합니다.

## 5분 실행

필요한 도구:

- Python 3.11 (`.python-version`으로 지정)
- [`uv`](https://docs.astral.sh/uv/getting-started/installation/)
- FFmpeg: M4A를 Streaming용 PCM으로 변환할 때 사용 예정

```bash
git clone https://github.com/bumfercar/StationAlert.git
cd StationAlert
uv sync --extra dev
uv run nextstop --help
uv run pytest
uv run ruff check .
```

현재 실행 가능한 명령:

```bash
uv run nextstop version
```

예상 결과:

```text
0.1.0
```

## 왜 Streaming STT인가

목적지 알림은 전체 녹음이 끝난 뒤 전사하는 것보다 안내방송이 끝나는 시점에 빠르게
판단하는 것이 중요합니다. 그래서 기본 실행 경로는 RTZR 일반 STT가 아니라 Streaming
STT입니다.

일반 STT는 동일한 음성 구간을 파일 단위로 비교할 때 참고 결과로 사용할 수 있지만,
실시간 알림 지연시간을 평가하는 기준으로 사용하지 않습니다.

## 실험 계획

모든 설정은 사람이 직접 들으며 만든 동일한 정답 구간으로 비교합니다. 한 번에 많은
조합을 돌리지 않고, 설정을 하나씩 추가하여 어떤 요소가 결과를 바꿨는지 확인합니다.

| 단계 | RTZR 설정 | 추가 기능 | 확인할 내용 |
| --- | --- | --- | --- |
| A | `sommers_ko` + `CALL` | 최종 결과의 정확한 역명 일치 | 기본 성능 |
| B | `sommers_ko` + `MEETING` | 동일 | 원거리 마이크용 도메인의 영향 |
| C | B + 적정 키워드 점수 | 동일 | 역명 인식 개선과 오인식 증가 여부 |
| D | C | 안내방송 문맥 판단 | 승객 대화·환승 안내 오탐 감소 여부 |
| E | D | 7호선 역 순서 확인 | 불가능한 역 이동 오탐 감소 여부 |
| F | RTZR Streaming `whisper` | E와 동일 | RTZR 모델별 오류와 지연 차이 |

외부 Whisper 비교는 위 실험이 완성된 뒤에만 추가합니다. 우선 RTZR가 제공하는 모델,
도메인, 키워드 기능을 같은 조건에서 비교하는 것이 RTZR STT를 이해하는 데 더 직접적인
근거가 되기 때문입니다.

## 평가 기준

F1을 계산할 때 단위를 명확히 정합니다.

- 전사 평가는 사람이 작성한 안내방송 한 구간을 단위로 합니다.
- 알림 평가는 목적지 방송이 있거나 없는 한 번의 판단 기회를 단위로 합니다.
- `TP`, `FP`, `FN`, `TN`을 먼저 저장한 뒤 Precision, Recall, F1을 계산합니다.
- 정답이 없는 구간, 승객 대화, 비슷한 역명도 음성에 포함해 오탐을 확인합니다.
- 실패한 결과도 삭제하지 않고 설정과 원본 응답을 함께 보관합니다.

## 구현 구조

```text
비공개 M4A 또는 공개 샘플
  -> FFmpeg로 mono LINEAR16 PCM 변환
  -> 실제 재생 시간에 맞춘 raw audio frame
  -> RTZR Streaming STT WebSocket
  -> partial/final 응답 및 시간 정보 저장
  -> 한국어 텍스트 정규화
  -> 안내방송 종류 판단
  -> 7호선 역 순서 확인
  -> 판단 이유가 포함된 알림
```

STT 연결 코드와 지하철 알림 규칙을 분리합니다. 이를 통해 같은 전사 결과에 단순 역명
일치, 안내방송 문맥, 노선 순서 검증을 차례로 적용하고 효과를 따로 측정할 수 있습니다.

## M4A 입력 처리

보유한 7호선 녹음은 M4A이므로 입력 파일로 사용할 수 있습니다. 다만 M4A 파일 자체를
Streaming API에 보내지는 않습니다.

FFmpeg로 다음 조건의 raw PCM을 만들 예정입니다.

- mono
- signed 16-bit little-endian PCM
- RTZR에 명시적으로 전달한 sample rate
- WAV의 `RIFF` header가 없는 raw frame

전송 테스트에서는 첫 바이트에 WAV header가 포함되지 않는지와 전송 속도가 실제 음성
시간과 맞는지 확인합니다.

## 비밀정보와 음성 파일 보호

`.env.example`을 참고해 로컬 `.env`를 만들거나 환경변수를 직접 설정합니다.

```bash
export RTZR_CLIENT_ID="..."
export RTZR_CLIENT_SECRET="..."
```

다음 파일은 Git에 포함하지 않습니다.

- 실제 `.env`와 인증 토큰
- `private_audio/` 아래의 원본 녹음
- `results/private/` 아래의 비공개 실험 결과
- `reports/private/` 아래의 제출용 보고서
- 승객 음성, 개인 정보, 로컬 절대경로

## 관련 문서

- [프로젝트 및 실험 설계](docs/PROJECT_SPEC.md)
- [Git 작업 방식](docs/GIT_WORKFLOW.md)
- [AI 활용 및 시행착오](docs/ai-workflow.md)

## 아직 하지 않은 것

- 실제 RTZR API 연결과 응답 확인
- M4A 구간 분리 및 사람 정답 작성
- 목적지 판단 규칙과 7호선 노선 상태 구현
- `CALL`/`MEETING`, 키워드, 모델 비교
- 측정 결과와 실패 사례 작성

측정 전에는 어떤 설정이 더 좋다고 결론내리지 않습니다.

## 참고한 공식 문서

- [RTZR 인증](https://developers.rtzr.ai/docs/authentications/)
- [RTZR Streaming STT](https://developers.rtzr.ai/docs/stt-streaming/)
- [RTZR Streaming WebSocket](https://developers.rtzr.ai/docs/stt-streaming/websocket/)
- [RTZR 일반 STT](https://developers.rtzr.ai/docs/stt-file/)
