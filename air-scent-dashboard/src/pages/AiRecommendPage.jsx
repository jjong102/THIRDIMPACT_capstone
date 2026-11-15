import { useState, useCallback, useEffect, useRef } from "react";
import {
  Sparkles,
  SprayCan,
  Mic,
  AlertTriangle,
  CheckCircle2,
  Volume2,
} from "lucide-react";

import { useCurrentTime } from "../hooks/useCurrentTime";
import useSpeechInput, {
  getSpeechErrorMessage,
  isSecureSpeechContext,
} from "../hooks/useSpeechInput";
import useTtsSpeak from "../hooks/useTtsSpeak";
import { getRecipeById, getRecipeVisuals, scentDisplayName, scentLabel, localizeScentText } from "../data/scentRecipes";
import {
  requestScentRecommendation,
  validateDispenseRequest,
} from "../services/llmRecommend";
import { validateDispenseRequest as localValidate } from "../utils/fragranceValidation";
import "./AiRecommendPage.css";

const CHAT_STEPS = [
  {
    key: "needNow",
    aiPrompt: "지금 기분은 어떠세요? 편하게 말씀해 주세요.",
    micHint: "마이크를 한 번 누르고 지금 느낌을 말해 주세요",
  },
];

const INITIAL_FORM = {
  needNow: "",
  currentMood: "",
  desiredState: "",
  scentPreference: "",
  intensityPreference: 2,
  intensityText: "",
};

function parseIntensity(text) {
  const normalized = String(text || "").replace(/\s/g, "");
  if (/약|약함|은은|가볍|살짝/.test(normalized)) {
    return 1;
  }
  if (/강|강함|진|세|진하게|많이/.test(normalized)) {
    return 3;
  }
  return 2;
}

function isAffirmative(text) {
  const normalized = String(text || "").replace(/\s/g, "");
  return /네|예|응|좋아|그래|맞아|그걸로|그거로|해줘|시작해|분사|오케이|오키|ok|yes/i.test(
    normalized
  );
}

function isNegative(text) {
  const normalized = String(text || "").replace(/\s/g, "");
  return /아니|싫어|다른|다시|말고|아니요|no/i.test(normalized);
}

function spokenFromForm(userForm) {
  return String(
    userForm?.needNow || userForm?.desiredState || userForm?.currentMood || ""
  ).trim();
}

function stripTrailingAsk(text) {
  return String(text || "")
    .replace(/\s*(이걸로 하(시겠어요|실래요)|이 향 어떠세요|어떠세요|어때요)[?？]?\s*$/g, "")
    .trim();
}

function buildRecommendText(result, userForm) {
  const reason = localizeScentText(stripTrailingAsk(result.reason));
  if (reason) {
    return reason;
  }

  const spoken = spokenFromForm(userForm);
  const name = scentLabel(scentDisplayName(result.recipe_name));
  if (spoken) {
    return `${spoken}라고 하셔서 ${name} 향이 좋을 것 같아요.`;
  }
  return `${name} 향이 지금 맞을 것 같아요.`;
}

function buildConfirmQuestion(wardName) {
  if (wardName) {
    return `${wardName}에는 이게 좋을 것 같은데, 어떠세요?`;
  }
  return "이게 좋을 것 같은데, 어떠세요?";
}

function wait(ms) {
  return new Promise((resolve) => {
    setTimeout(resolve, ms);
  });
}

function ScentVisualRow({ recipeId, size = "md" }) {
  const visuals = getRecipeVisuals(recipeId);

  return (
    <div className={`ai-scent-visuals ${size}`} aria-hidden="true">
      <div className="ai-scent-emoji-row">
        {visuals.emojis.map((emoji, index) => (
          <span
            key={`${recipeId}-${emoji}`}
            className="ai-scent-emoji"
            style={{ animationDelay: `${index * 0.15}s` }}
          >
            {emoji}
          </span>
        ))}
      </div>
      <span className="ai-scent-mood">{visuals.mood}</span>
    </div>
  );
}

function AiRecommendPage({
  recommendedFragrance,
  recommendedReason,
  data,
  mqttAirQuality,
  fragranceCartridgeRemaining,
  lastDispenseAt,
  targetWardName,
  targetWardId,
  initialView = "recommend",
  onBack,
  onApplyRecommendation,
  seedPrompt = "",
}) {
  const [view, setView] = useState(initialView);
  const [form, setForm] = useState(INITIAL_FORM);
  const [chatMessages, setChatMessages] = useState([]);
  const [chatStatus, setChatStatus] = useState("idle");
  const [stepIndex, setStepIndex] = useState(-1);
  const [recommendation, setRecommendation] = useState(null);
  const [validation, setValidation] = useState(null);
  const [error, setError] = useState(null);
  const chatEndRef = useRef(null);
  const chatSessionRef = useRef(0);
  const stepTimerRef = useRef(null);
  const formRef = useRef(form);
  const confirmDispenseRef = useRef(null);
  const retryChatRef = useRef(null);
  const { period, timeShort } = useCurrentTime(60000);
  const { speaking, speak, stop } = useTtsSpeak();

  formRef.current = form;

  const appendMessage = useCallback((message) => {
    setChatMessages((prev) => [...prev, message]);
    if (message.role === "ai" && message.text) {
      speak(message.text);
    }
  }, [speak]);

  const buildContextPayload = useCallback(
    (userForm) => ({
      userInput: userForm,
      bme688: {
        airStatus: data.airStatus,
        airQuality: data.airQuality,
        pm25: data.pm25,
        humidity: data.humidity,
        temperature: data.temperature,
        gasResistance: data.gasResistance,
      },
      mqtt: {
        label: mqttAirQuality?.label,
        confidence: mqttAirQuality?.confidence,
      },
      timePeriod: period,
      cartridgeRemaining: fragranceCartridgeRemaining,
      lastDispenseAt,
      context: {
        bme688: { humidity: data.humidity },
      },
    }),
    [data, mqttAirQuality, period, fragranceCartridgeRemaining, lastDispenseAt]
  );

  const runValidation = useCallback(
    (rec) => {
      const payload = {
        recipe_id: rec.recipe_id ?? rec.recipeId,
        recipeId: rec.recipe_id ?? rec.recipeId,
        intensity: rec.intensity,
        duration_seconds: rec.duration_seconds ?? rec.durationSeconds,
        durationSeconds: rec.duration_seconds ?? rec.durationSeconds,
        cartridgeRemaining: fragranceCartridgeRemaining,
        lastDispenseAt,
        context: {
          bme688: { humidity: data.humidity },
        },
      };

      const local = localValidate(payload);
      validateDispenseRequest(payload)
        .then((serverResult) => {
          setValidation({
            ...serverResult,
            valid: serverResult.valid && local.valid,
            errors: [...new Set([...local.errors, ...serverResult.errors])],
            warnings: [...new Set([...local.warnings, ...serverResult.warnings])],
          });
        })
        .catch(() => {
          setValidation(local);
        });
    },
    [data.humidity, fragranceCartridgeRemaining, lastDispenseAt]
  );

  const fetchRecommendation = useCallback(
    async (userForm) => {
      const sessionId = chatSessionRef.current;
      setChatStatus("thinking");

      appendMessage({
        role: "ai",
        text: "말씀 잘 들었어요. 방금 말한 기분에 맞는 향을 고를게요.",
      });

      await wait(400);
      if (chatSessionRef.current !== sessionId) {
        return;
      }

      try {
        const result = await requestScentRecommendation(buildContextPayload(userForm));
        if (chatSessionRef.current !== sessionId) {
          return;
        }

        setRecommendation(result);
        runValidation(result);

        appendMessage({
          role: "ai",
          text: buildRecommendText(result, userForm),
          highlight: true,
        });
        await wait(500);
        if (chatSessionRef.current !== sessionId) {
          return;
        }
        appendMessage({
          role: "ai",
          text: buildConfirmQuestion(targetWardName),
        });
        setChatStatus("awaitingConfirm");
        setError(null);
      } catch (err) {
        if (chatSessionRef.current !== sessionId) {
          return;
        }

        setError(err.message || "추천 요청에 실패했습니다.");
        appendMessage({
          role: "ai",
          text: "추천 중 오류가 발생했어요. 다시 시도해 주세요.",
        });
        setChatStatus("listening");
      }
    },
    [appendMessage, buildContextPayload, runValidation, targetWardName]
  );

  const advanceStep = useCallback(
    (userText) => {
      const step = CHAT_STEPS[stepIndex];
      if (!step) {
        return;
      }

      appendMessage({ role: "user", text: userText });

      const nextForm = { ...formRef.current };
      nextForm[step.key] = userText;
      if (step.key === "needNow") {
        nextForm.currentMood = userText;
        nextForm.desiredState = userText;
      }
      nextForm.intensityPreference = parseIntensity(
        `${nextForm.needNow} ${nextForm.scentPreference}`
      );
      if (/약|강|은은|진하게/.test(userText.replace(/\s/g, ""))) {
        nextForm.intensityText = userText;
      }
      setForm(nextForm);
      formRef.current = nextForm;

      const nextIndex = stepIndex + 1;
      if (nextIndex < CHAT_STEPS.length) {
        setStepIndex(nextIndex);
        setChatStatus("thinking");

        if (stepTimerRef.current) {
          clearTimeout(stepTimerRef.current);
        }

        const sessionId = chatSessionRef.current;
        stepTimerRef.current = setTimeout(() => {
          stepTimerRef.current = null;
          if (chatSessionRef.current !== sessionId) {
            return;
          }
          appendMessage({ role: "ai", text: CHAT_STEPS[nextIndex].aiPrompt });
          setChatStatus("listening");
        }, 600);
        return;
      }

      fetchRecommendation(nextForm);
    },
    [stepIndex, appendMessage, fetchRecommendation]
  );

  const handleSpeechResult = useCallback(
    (transcript) => {
      if (chatStatus === "awaitingConfirm") {
        appendMessage({ role: "user", text: transcript });
        if (isAffirmative(transcript)) {
          confirmDispenseRef.current?.();
          return;
        }
        if (isNegative(transcript)) {
          retryChatRef.current?.();
          return;
        }
        appendMessage({
          role: "ai",
          text: "이 향으로 할지 네 또는 아니오로 답해 주세요.",
        });
        return;
      }

      if (chatStatus !== "listening" || stepIndex < 0) {
        return;
      }
      advanceStep(transcript);
    },
    [chatStatus, stepIndex, advanceStep, appendMessage]
  );

  const { listening, processing, supported, startListening, stopListening } = useSpeechInput({
    onResult: handleSpeechResult,
    onError: (code) => {
      const known = [
        "not-allowed",
        "service-not-allowed",
        "audio-capture",
        "no-speech",
        "too-quiet",
        "stt-unavailable",
        "still-processing",
        "device-busy",
        "unknown",
      ];
      setError(known.includes(code) ? getSpeechErrorMessage(code) : getSpeechErrorMessage("unknown"));
    },
  });

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [chatMessages, chatStatus, recommendation, validation, listening, processing, speaking]);

  const startChatSession = useCallback(() => {
    if (stepTimerRef.current) {
      clearTimeout(stepTimerRef.current);
      stepTimerRef.current = null;
    }

    stop();

    const sessionId = chatSessionRef.current + 1;
    chatSessionRef.current = sessionId;

    setForm(INITIAL_FORM);
    formRef.current = INITIAL_FORM;
    setChatMessages([]);
    setRecommendation(null);
    setValidation(null);
    setError(null);
    setStepIndex(0);
    setChatStatus("thinking");

    stepTimerRef.current = setTimeout(() => {
      stepTimerRef.current = null;
      if (chatSessionRef.current !== sessionId) {
        return;
      }
      appendMessage({
        role: "ai",
        text: targetWardName
          ? `${targetWardName}에 맞는 향을 찾아볼게요. ${CHAT_STEPS[0].aiPrompt}`
          : CHAT_STEPS[0].aiPrompt,
      });
      setChatStatus("listening");
    }, 400);
  }, [appendMessage, stop, targetWardName]);

  const startFromText = useCallback(
    (text) => {
      const spoken = String(text || "").trim();
      if (!spoken) return;

      if (stepTimerRef.current) {
        clearTimeout(stepTimerRef.current);
        stepTimerRef.current = null;
      }

      stop();

      const sessionId = chatSessionRef.current + 1;
      chatSessionRef.current = sessionId;

      const nextForm = {
        ...INITIAL_FORM,
        needNow: spoken,
        currentMood: spoken,
        desiredState: spoken,
        intensityPreference: parseIntensity(spoken),
      };
      setForm(nextForm);
      formRef.current = nextForm;
      setChatMessages([{ role: "user", text: spoken }]);
      setRecommendation(null);
      setValidation(null);
      setError(null);
      setStepIndex(0);
      setChatStatus("thinking");
      fetchRecommendation(nextForm);
    },
    [fetchRecommendation, stop]
  );

  retryChatRef.current = startChatSession;

  const startChatSessionRef = useRef(startChatSession);
  const startFromTextRef = useRef(startFromText);
  startChatSessionRef.current = startChatSession;
  startFromTextRef.current = startFromText;

  useEffect(() => {
    if (view !== "chat") {
      return undefined;
    }

    // 센서·MQTT가 바뀔 때마다 콜백이 새로 생겨도 진행 중인 추천을 취소하지 않는다.
    const typed = String(seedPrompt || "").trim();
    if (typed) {
      startFromTextRef.current(typed);
    } else {
      startChatSessionRef.current();
    }

    return () => {
      if (stepTimerRef.current) {
        clearTimeout(stepTimerRef.current);
        stepTimerRef.current = null;
      }
      chatSessionRef.current += 1;
      stop();
    };
  }, [view, seedPrompt, stop]);

  const handleMicClick = async (event) => {
    event.preventDefault();

    if (listening) {
      stopListening();
      return;
    }

    if (processing) {
      return;
    }

    if (
      (chatStatus !== "listening" && chatStatus !== "awaitingConfirm") ||
      stepIndex < 0
    ) {
      return;
    }

    if (!supported) {
      setError("이 브라우저는 마이크 입력을 지원하지 않아요.");
      return;
    }

    if (!isSecureSpeechContext()) {
      setError(getSpeechErrorMessage("service-not-allowed"));
      return;
    }

    stop();
    setError(null);
    await startListening();
  };

  const handleConfirmDispense = async () => {
    if (!recommendation) {
      return;
    }
    if (!validation) {
      appendMessage({
        role: "ai",
        text: "확인이 조금 남았어요. 잠시 뒤에 다시 네라고 해 주세요.",
      });
      return;
    }
    if (!validation.valid) {
      appendMessage({
        role: "ai",
        text: "지금은 이 향으로 분사가 어려워요. 다시 추천받아 볼까요?",
      });
      return;
    }

    setChatStatus("confirming");

    try {
      const sessionAtConfirm = chatSessionRef.current;
      const recipe = getRecipeById(recommendation.recipe_id);
      const scentName = scentDisplayName(
        recipe?.displayName || recommendation.recipe_name
      );
      const applyPayload = {
        fragrance: scentName,
        recipeId: recommendation.recipe_id,
        blend: recipe?.blend,
        intensity: recommendation.intensity,
        durationSeconds: validation.adjustedDuration ?? recommendation.duration_seconds,
        reason: recommendation.reason,
        mqttSent: false,
        startNow: false,
        wardId: targetWardId,
        wardName: targetWardName,
      };
      const savedText = targetWardName
        ? `${scentLabel(scentName)}로 설정해 둘게요. ${targetWardName}에 가서 발향 시작을 눌러 주세요.`
        : `${scentLabel(scentName)}로 설정해 둘게요. 위치에서 발향 시작을 눌러 주세요.`;

      onApplyRecommendation?.({
        ...applyPayload,
        persistOnly: true,
      });

      appendMessage({
        role: "ai",
        text: savedText,
      });
      await wait(3200);
      if (chatSessionRef.current !== sessionAtConfirm) {
        return;
      }

      onApplyRecommendation?.(applyPayload);

      if (onBack) {
        onBack();
        return;
      }

      setView("recommend");
    } catch (err) {
      setError(err.message || "향 설정 저장에 실패했습니다.");
      setChatStatus("awaitingConfirm");
    }
  };

  confirmDispenseRef.current = handleConfirmDispense;

  const closeChat = () => {
    stop();
    chatSessionRef.current += 1;
    if (stepTimerRef.current) {
      clearTimeout(stepTimerRef.current);
      stepTimerRef.current = null;
    }

    setChatStatus("idle");
    setChatMessages([]);
    setRecommendation(null);
    setValidation(null);
    setError(null);
    setStepIndex(-1);
    setForm(INITIAL_FORM);

    if (onBack) {
      onBack();
      return;
    }

    setView("recommend");
  };

  const micRingClass =
    listening
      ? "listening"
      : processing || chatStatus === "thinking" || chatStatus === "confirming"
        ? "thinking"
        : "";

  const showUserTyping =
    listening ||
    (processing && (chatStatus === "listening" || chatStatus === "awaitingConfirm"));
  const showAiTyping =
    (chatStatus === "thinking" || chatStatus === "confirming") && !showUserTyping;

  const micStatusText = {
    idle: "대화를 시작해 주세요",
    listening: listening
      ? "듣고 있어요... 다 말하면 자동으로 전송돼요"
      : speaking
        ? "AI가 말하고 있어요"
        : CHAT_STEPS[stepIndex]?.micHint ?? "마이크를 한 번 누르고 말씀해 주세요...",
    thinking: processing
      ? "음성을 텍스트로 변환 중..."
      : speaking
        ? "AI가 말하고 있어요"
        : "AI가 분석 중이에요",
    awaitingConfirm: listening
      ? "듣고 있어요... 다 말하면 자동으로 전송돼요"
      : speaking
        ? "AI가 말하고 있어요"
        : "네 또는 아니오로 답해 주세요",
    confirming: "분사 명령 전송 중...",
  };

  const showConfirmActions = chatStatus === "awaitingConfirm" && recommendation;
  const micEnabled =
    (chatStatus === "listening" || chatStatus === "awaitingConfirm") && !processing;

  if (view === "chat") {
    return (
      <section className="ai-page ai-chat-page">
        <article className="ai-chat-card">
          <div className="ai-chat-body">
            <div className="ai-chat-messages">
              {chatMessages.map((message, index) => {
                const lastAiIndex = chatMessages.findLastIndex(
                  (item) => item.role === "ai"
                );
                const isSpeakingBubble =
                  speaking && message.role === "ai" && index === lastAiIndex;

                return (
                  <div
                    key={`${message.role}-${index}`}
                    className={`ai-chat-bubble ${message.role}${
                      message.highlight ? " highlight" : ""
                    }`}
                  >
                    {message.role === "ai" && (
                      <div className={`ai-chat-avatar${isSpeakingBubble ? " speaking" : ""}`}>
                        {isSpeakingBubble ? <Volume2 size={16} /> : <Sparkles size={16} />}
                      </div>
                    )}
                    <div className="ai-chat-bubble-content">
                      {message.highlight && recommendation?.recipe_id && (
                        <ScentVisualRow recipeId={recommendation.recipe_id} size="sm" />
                      )}
                      <p>{message.text}</p>
                    </div>
                  </div>
                );
              })}

              {showUserTyping && (
                <div className="ai-chat-bubble user typing">
                  <div className="ai-typing-dots user">
                    <span />
                    <span />
                    <span />
                  </div>
                </div>
              )}

              {showAiTyping && (
                <div className="ai-chat-bubble ai typing">
                  <div className="ai-chat-avatar">
                    <Sparkles size={16} />
                  </div>
                  <div className="ai-typing-dots">
                    <span />
                    <span />
                    <span />
                  </div>
                </div>
              )}

              {validation && chatStatus === "awaitingConfirm" && (
                <div className={`ai-validation inline ${validation.valid ? "ok" : "fail"}`}>
                  {validation.valid ? (
                    <CheckCircle2 size={18} />
                  ) : (
                    <AlertTriangle size={18} />
                  )}
                  <div>
                    {validation.errors.map((msg) => (
                      <p key={msg} className="ai-validation-error">
                        {msg}
                      </p>
                    ))}
                    {validation.warnings.map((msg) => (
                      <p key={msg} className="ai-validation-warn">
                        {msg}
                      </p>
                    ))}
                    {validation.valid && <p>이 향으로 설정할 수 있어요. 발향은 시작 버튼을 누른 뒤에 합니다.</p>}
                  </div>
                </div>
              )}

              {error && (
                <div className="ai-alert error">
                  <AlertTriangle size={18} />
                  <span>{error}</span>
                </div>
              )}

              <div ref={chatEndRef} />
            </div>

            <div className="ai-chat-mic-area">
              {showConfirmActions && (
                <div className="ai-confirm-choice-row" role="group" aria-label="추천 확인">
                  <button
                    type="button"
                    className="ai-scent-choice"
                    disabled={!validation?.valid}
                    onClick={handleConfirmDispense}
                  >
                    네, 이걸로 설정할게요
                  </button>
                  <button
                    type="button"
                    className="ai-scent-choice"
                    onClick={startChatSession}
                  >
                    다시 추천받기
                  </button>
                </div>
              )}

              {chatStatus !== "confirming" && (
                <>
                  <button
                    type="button"
                    className={`ai-mic-ring ${micRingClass}`}
                    disabled={!micEnabled}
                    onClick={handleMicClick}
                    aria-label={listening ? "녹음 중지하고 전송하기" : "마이크를 한 번 누르고 말하기"}
                  >
                    <div className="ai-mic-button">
                      <Mic size={36} strokeWidth={2.2} />
                    </div>
                  </button>

                  <p className="ai-mic-status">{micStatusText[chatStatus] ?? ""}</p>
                </>
              )}
            </div>
          </div>
        </article>
      </section>
    );
  }

  return (
    <section className="ai-page">
      <article className="ai-recommend-card">
        <div>
          <p className="section-label">추천 향기</p>
          <p className="ai-small-text">
            {targetWardName ? `지금 ${targetWardName}에 어울리는` : "지금 이곳에 어울리는"}
          </p>
          <h1>{scentLabel(recommendedFragrance)} ✨</h1>
        </div>

        <div className="ai-object">
          <SprayCan size={52} strokeWidth={2.1} />
        </div>

        <div className="ai-button-row">
          <button
            className="ai-main-button ai-recommend-button"
            type="button"
            onClick={() => setView("chat")}
          >
            AI 향 추천받기
          </button>
        </div>
      </article>

      <div className="ai-side-grid">
        <article className="ai-info-card">
          <p className="section-label">현재 환경 요약</p>

          <div className="ai-info-list">
            <div>
              <span>공기 상태</span>
              <strong>
                {data.airStatus} / AQI {data.airQuality}
              </strong>
            </div>

            <div>
              <span>습도</span>
              <strong>{data.humidity}%</strong>
            </div>

            <div>
              <span>냄새 분류</span>
              <strong>
                {mqttAirQuality?.label ?? "—"}
                {mqttAirQuality?.confidence != null &&
                  ` (${mqttAirQuality.confidence}%)`}
              </strong>
            </div>

            <div>
              <span>시간대</span>
              <strong>
                {period} / {timeShort}
              </strong>
            </div>
          </div>
        </article>

        <article className="ai-reason-card">
          <div className="reason-icon">
            <Sparkles size={32} />
          </div>

          <p className="section-label">추천 이유</p>
          <h2>
            {(recommendedReason && localizeScentText(recommendedReason)) ??
              "AI 추천을 받으면 여기에 이유가 표시됩니다."}
          </h2>
        </article>
      </div>
    </section>
  );
}

export default AiRecommendPage;
