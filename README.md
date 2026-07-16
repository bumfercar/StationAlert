# NextStop STT

NextStop STT는 실제 지하철 녹음을 RTZR Streaming STT로 재생하고, final 응답에서
현재 역명을 추출하는 Python CLI 데모입니다. 전체 전사문을 그대로 보여주는 것보다
`안내방송 인식 → 역명 추출 → 근거 출력` 과정을 재현 가능하게 만드는 데 집중했습니다.

| 평가 대상 | 확인할 질문 | 지표 |
| --- | --- | --- |
| 음성인식 | 안내방송과 역명을 정확히 전사했는가? | CER, 역명 CER, 역명 일치율 |
| 역명 추출 | 현재 역명을 정확한 토큰으로 찾았는가? | 역명 일치율, 추출 근거 |
| 실시간성 | 안내방송을 final 응답으로 언제 확정했는가? | partial/final 수, 응답 시각 |

> **현재 상태:** RTZR 인증, M4A 실시간 재생, Streaming WebSocket, Batch 비교,
> 역명 추출, 비공개 결과 저장과 offline test를 구현했습니다. 아래 수치는 동일한 실제
> 20초 구간을 반복 실행한 관찰값이며 전체 노선 성능으로 일반화하지 않습니다.

## 5분 실행

필요한 도구:

- Python 3.11 (`.python-version`으로 지정)
- [`uv`](https://docs.astral.sh/uv/getting-started/installation/)
- FFmpeg: M4A를 raw LINEAR16 frame으로 변환

```bash
git clone https://github.com/bumfercar/StationAlert.git
cd StationAlert
uv sync --extra dev
uv run nextstop --help
uv run pytest
uv run ruff check .
```

`.env.example`을 복사하고 RTZR credential을 입력합니다. `.env`를 자동으로 읽지는 않으므로
실행 shell에 환경변수를 설정해야 합니다.

```bash
export RTZR_CLIENT_ID="..."
export RTZR_CLIENT_SECRET="..."
uv run nextstop check-auth
```

Windows PowerShell에서는 다음처럼 설정합니다.

```text
$env:RTZR_CLIENT_ID="..."
$env:RTZR_CLIENT_SECRET="..."
uv run nextstop check-auth
```

## 현재역 추출 데모

직접 녹음했거나 사용 권한이 있는 파일을 지정합니다. `duration-ms`를 필수로 두어 실수로
긴 유료 호출을 실행하지 않도록 했습니다.

```bash
uv run nextstop stream-file --source-file path/to/recording.m4a --start-ms 1124000 --duration-ms 20000 --domain MEETING --model sommers_ko --line7-keyword-score 1.0 --detect-station --output-file results/private/demo-current-station.json
```

실제 확인한 출력은 다음과 같습니다. 기본값에서는 승객 음성이 포함될 수 있는 전체
전사문을 출력하지 않고, 역명과 판단 근거만 보여줍니다.

```text
CURRENT_STATION: 어린이대공원 reason=canonical_with_alias_and_station_suffix seq=2
Streaming completed: partial=12, final=5, stations=1, candidates=1, alerts=0
```

`어린이대공원 세종대 역`처럼 본역명과 부역명 사이에 `역`이 오거나, STT가 마지막
`역`만 놓친 `어린이대공원 세종대`도 알려진 부역명 조합이므로 정답으로
처리합니다. `--line7-keyword-score`는 정답 역 하나가 아니라 녹음 구간의 모든 역명과
부역명을 같은 점수로 등록합니다. 반면 `어린이비복원` 같은 유사 문자열은 fuzzy
match하지 않습니다. 이 실행에서는 `중화` bare token도 1건 나왔지만 `역` 근거가 없어
현재역으로 출력하지 않고 검토 후보로만 저장했습니다.

## 왜 Streaming STT인가

현재역 안내는 전체 녹음이 끝난 뒤 전사하는 것보다 방송이 끝나는 시점에 역명을
확정하는 것이 중요합니다. 그래서 기본 실행 경로는 RTZR 일반 STT가 아니라 Streaming
STT입니다.

일반 STT는 동일한 음성 구간을 파일 단위로 비교할 때 참고 결과로 사용할 수 있지만,
실시간 알림 지연시간을 평가하는 기준으로 사용하지 않습니다.

## 도메인과 keyword score 선택

대상은 지하철 객실에서 스마트폰으로 녹음한 원거리 안내방송입니다. 공식 문서상
`CALL`은 근접 마이크, `MEETING`은 원거리·공개 장소에 적합합니다. 문서 설명만으로
결정하지 않고 동일한 어린이대공원 주변 20초에 한 요소씩 바꿔 실제 응답을 비교했습니다.

| Domain | Keyword score | partial/final | 목표역 final | 관찰 |
| --- | ---: | ---: | --- | --- |
| `CALL` | 없음 | 1/1 | 빈 문자열 | 원거리 안내방송을 거의 검출하지 못함 |
| `MEETING` | 없음 | 12/5 | `어린이비복원 세동제 역` | 방송 구간은 분리했지만 역명 오인식 |
| `MEETING` | 1.0 | 12/5 | `어린이대공원 세종대 역` | 정확히 복원 |
| `MEETING` | 2.0 | 12/5 | `어린이대공원 세종대 역` | 정확하지만 1.0보다 이점이 확인되지 않음 |
| `MEETING` | 3.0 | 12/5 | `어린이대공원 세종대` | 뒤 구간에도 `세종`이 나타나 과한 편향 징후 |

따라서 이 녹음의 데모 기본 설정은 `MEETING + 1.0`입니다. 1.0과 2.0이 같은 목표역을
복원했으므로 더 낮은 값을 선택해 불필요한 keyword 편향을 줄였습니다. 이 결론은 한
구간의 결과이며, 전체 역에 대한 최적 score라고 주장하지 않습니다.

## 평가 기준

평가 단위를 다음처럼 분리합니다.

- 전사 평가는 사람이 작성한 안내방송 한 구간을 단위로 합니다.
- 역명 평가는 `역` 접미사가 없는 정확한 역명 토큰도 인식 성공으로 봅니다.
- 현재역 근거는 `역명+역`, `역명+부역명(+역)`처럼 제한된 강한 패턴을 사용합니다.
- 목적지 알림 평가는 별도 확장 기능이며 현재역 인식 성공과 혼동하지 않습니다.
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
  -> final 응답만 역명 추출
  -> 본역명·부역명 패턴 판정
  -> 현재역과 machine-readable reason 출력
```

RTZR 연결과 역명 추출을 분리했습니다. 저장된 응답으로 추출 규칙을 offline test할 수
있고, STT 인식 오류와 도메인 규칙 오류를 따로 설명할 수 있습니다.

## M4A 입력 처리

보유한 7호선 녹음은 M4A이므로 입력 파일로 사용할 수 있습니다. 다만 M4A 파일 자체를
Streaming API에 보내지는 않습니다.

FFmpeg adapter가 실행 중 다음 조건의 raw PCM을 생성합니다.

- mono
- signed 16-bit little-endian PCM
- RTZR에 명시적으로 전달한 sample rate
- WAV의 `RIFF` header가 없는 raw frame

첫 frame이 `RIFF`로 시작하면 전송을 거부하며, frame 전송 속도는 실제 음성 시간에
맞춥니다. Python 코드는 shell 문자열을 조립하지 않고 FFmpeg 인자를 직접 전달합니다.

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

## 현재 한계와 다음 검증

- 현재역 데모는 노원부터 어린이대공원까지의 7호선 역 목록을 사용합니다.
- score 비교는 어린이대공원 20초 한 구간의 관찰이므로 더 많은 역에서 재검증해야 합니다.
- 광고가 포함된 긴 구간과 알아듣기 어려운 사람 정답은 CER에서 명시적으로 제외합니다.
- fuzzy match는 오탐 위험 때문에 사용하지 않았습니다.
- 원본 녹음과 전사 결과는 개인정보 보호를 위해 공개 저장소에 포함하지 않습니다.

## 참고한 공식 문서

- [RTZR 인증](https://developers.rtzr.ai/docs/authentications/)
- [RTZR Streaming STT](https://developers.rtzr.ai/docs/stt-streaming/)
- [RTZR Streaming WebSocket](https://developers.rtzr.ai/docs/stt-streaming/websocket/)
- [RTZR 일반 STT](https://developers.rtzr.ai/docs/stt-file/)
