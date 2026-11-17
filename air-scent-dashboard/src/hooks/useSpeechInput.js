import { useState, useCallback, useRef } from "react";

import { transcribeAudio } from "../services/sttTranscribe";
import { encodeWav, mergeFloat32, resampleTo16k } from "../utils/audioWav";

const MIN_MS = 450;
const MIN_RMS = 0.004;
const SILENCE_STOP_MS = 900;
const MAX_LISTEN_MS = 8000;
const NO_VOICE_MS = 6000;
const STT_TIMEOUT_MS = 25000;
const MIC_HINTS = ["music-boost", "musicboost", "usb microphone", "usb"];

function delay(ms) {
  return new Promise((resolve) => {
    window.setTimeout(resolve, ms);
  });
}

export function getSpeechErrorMessage(errorCode) {
  switch (errorCode) {
    case "not-allowed":
      return "마이크 권한이 필요해요. 브라우저 주소창 옆 🔒에서 마이크를 허용해 주세요.";
    case "service-not-allowed": {
      const host =
        typeof window !== "undefined" &&
        window.location.hostname &&
        window.location.hostname !== "localhost" &&
        window.location.hostname !== "127.0.0.1"
          ? window.location.hostname
          : "192.168.10.1";
      return `다른 기기에서는 https://${host}:5173 으로 연 뒤, 인증서 경고를 허용하고 마이크를 켜 주세요.`;
    }
    case "audio-capture":
      return "마이크를 찾을 수 없어요. 장치 연결을 확인해 주세요.";
    case "no-speech":
      return "음성이 감지되지 않았어요. 마이크를 한 번 누르고 말씀해 주세요.";
    case "too-quiet":
      return "마이크 소리가 거의 안 들려요. USB 마이크가 꽂혀 있는지 확인해 주세요.";
    case "stt-unavailable":
      return "음성 인식 서버에 연결할 수 없어요. 대시보드를 재시작해 주세요.";
    case "still-processing":
      return "아직 이전 음성을 변환 중이에요. 잠깐만 기다려 주세요.";
    case "device-busy":
      return "스피커가 마이크를 쓰고 있어요. 다시 한 번 눌러 주세요.";
    default:
      return "음성 인식에 실패했어요. 마이크를 한 번 누르고 다시 말씀해 주세요.";
  }
}

export function isSecureSpeechContext() {
  if (typeof window === "undefined") {
    return false;
  }
  return window.isSecureContext;
}

function mapCaptureError(err) {
  const name = err?.name ?? "";
  if (name === "NotAllowedError" || name === "PermissionDeniedError") {
    return "not-allowed";
  }
  if (name === "NotFoundError") {
    return "audio-capture";
  }
  if (name === "NotReadableError" || name === "AbortError") {
    return "device-busy";
  }
  if (name === "OverconstrainedError") {
    return "audio-capture";
  }
  return name || "unknown";
}

function mapTranscribeError(err) {
  const message = err?.message || "";
  if (
    message.includes("Failed to fetch") ||
    message.includes("NetworkError") ||
    message.includes("stt-unavailable")
  ) {
    return "stt-unavailable";
  }
  if (
    message.includes("no-speech") ||
    message.includes("감지되지") ||
    message.includes("너무 짧")
  ) {
    return "no-speech";
  }
  if (message.includes("음성 인식에 실패")) {
    return "stt-unavailable";
  }
  return message || "unknown";
}

function audioConstraints(deviceId) {
  return {
    echoCancellation: false,
    noiseSuppression: false,
    autoGainControl: true,
    ...(deviceId ? { deviceId: { exact: deviceId } } : {}),
  };
}

async function findPreferredMicId() {
  if (!navigator.mediaDevices?.enumerateDevices) {
    return "";
  }
  const devices = await navigator.mediaDevices.enumerateDevices();
  const inputs = devices.filter((device) => device.kind === "audioinput");
  const preferred = inputs.find((device) => {
    const label = String(device.label || "").toLowerCase();
    return MIC_HINTS.some((hint) => label.includes(hint));
  });
  return preferred?.deviceId || "";
}

async function openMicrophone() {
  const open = async (deviceId = "") => {
    try {
      return await navigator.mediaDevices.getUserMedia({
        audio: audioConstraints(deviceId),
      });
    } catch (err) {
      const name = err?.name ?? "";
      if (name === "NotReadableError" || name === "AbortError") {
        await delay(250);
        return await navigator.mediaDevices.getUserMedia({
          audio: audioConstraints(deviceId),
        });
      }
      throw err;
    }
  };

  const first = await open();
  try {
    const preferredId = await findPreferredMicId();
    const currentId = first.getAudioTracks()[0]?.getSettings?.()?.deviceId;
    if (preferredId && preferredId !== currentId) {
      first.getTracks().forEach((track) => track.stop());
      return await open(preferredId);
    }
  } catch {
    return first;
  }
  return first;
}

function rmsOf(samples) {
  if (!samples.length) {
    return 0;
  }
  let sum = 0;
  for (let i = 0; i < samples.length; i += 1) {
    const value = samples[i];
    sum += value * value;
  }
  return Math.sqrt(sum / samples.length);
}

function peakRms(samples, windowSize = 2048) {
  if (!samples.length) {
    return 0;
  }
  let peak = 0;
  for (let i = 0; i < samples.length; i += windowSize) {
    peak = Math.max(peak, rmsOf(samples.subarray(i, Math.min(i + windowSize, samples.length))));
  }
  return peak;
}

function withTimeout(promise, ms, errorCode = "stt-unavailable") {
  return new Promise((resolve, reject) => {
    const timer = window.setTimeout(() => {
      reject(new Error(errorCode));
    }, ms);
    promise.then(
      (value) => {
        window.clearTimeout(timer);
        resolve(value);
      },
      (error) => {
        window.clearTimeout(timer);
        reject(error);
      }
    );
  });
}

export default function useSpeechInput({ onResult, onError } = {}) {
  const [listening, setListening] = useState(false);
  const [processing, setProcessing] = useState(false);
  const supported =
    typeof navigator !== "undefined" && Boolean(navigator.mediaDevices?.getUserMedia);

  const streamRef = useRef(null);
  const audioContextRef = useRef(null);
  const processorRef = useRef(null);
  const sourceRef = useRef(null);
  const samplesRef = useRef([]);
  const startedAtRef = useRef(0);
  const holdingRef = useRef(false);
  const wantListenRef = useRef(false);
  const processingRef = useRef(false);
  const transcribeIdRef = useRef(0);
  const heardVoiceRef = useRef(false);
  const silenceMsRef = useRef(0);
  const stoppingRef = useRef(false);
  const stopListeningRef = useRef(() => {});
  const callbacksRef = useRef({ onResult, onError });

  callbacksRef.current = { onResult, onError };

  const stopTracks = useCallback(() => {
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
  }, []);

  const stopCaptureGraph = useCallback(() => {
    try {
      processorRef.current?.disconnect();
    } catch {
      // ignore
    }
    try {
      sourceRef.current?.disconnect();
    } catch {
      // ignore
    }
    processorRef.current = null;
    sourceRef.current = null;

    const context = audioContextRef.current;
    audioContextRef.current = null;
    if (context && context.state !== "closed") {
      context.close().catch(() => {});
    }
    stopTracks();
  }, [stopTracks]);

  const transcribeSamples = useCallback(async (samples, sampleRate) => {
    const durationMs = samples.length
      ? Math.round((samples.length / Math.max(sampleRate, 1)) * 1000)
      : 0;
    const energy = peakRms(samples);

    if (!samples.length || durationMs < MIN_MS) {
      callbacksRef.current.onError?.("no-speech");
      return;
    }
    if (energy < MIN_RMS) {
      callbacksRef.current.onError?.("too-quiet");
      return;
    }

    const id = (transcribeIdRef.current += 1);
    processingRef.current = true;
    setProcessing(true);

    try {
      const wav = encodeWav(resampleTo16k(samples, sampleRate), 16000);
      const result = await withTimeout(transcribeAudio(wav), STT_TIMEOUT_MS);
      if (id !== transcribeIdRef.current) {
        return;
      }
      const text = result.text?.trim();
      if (!text) {
        callbacksRef.current.onError?.("no-speech");
        return;
      }
      callbacksRef.current.onResult?.(text);
    } catch (err) {
      if (id !== transcribeIdRef.current) {
        return;
      }
      callbacksRef.current.onError?.(mapTranscribeError(err));
    } finally {
      if (id === transcribeIdRef.current) {
        processingRef.current = false;
        setProcessing(false);
      }
    }
  }, []);

  const finalizeRecording = useCallback(() => {
    const context = audioContextRef.current;
    const sampleRate = context?.sampleRate || 48000;
    const samples = mergeFloat32(samplesRef.current);
    samplesRef.current = [];
    setListening(false);
    stopCaptureGraph();
    transcribeSamples(samples, sampleRate);
  }, [stopCaptureGraph, transcribeSamples]);

  const startListening = useCallback(async () => {
    if (processingRef.current) {
      callbacksRef.current.onError?.("still-processing");
      return false;
    }

    if (!isSecureSpeechContext()) {
      callbacksRef.current.onError?.("service-not-allowed");
      return false;
    }

    if (typeof AudioContext === "undefined") {
      callbacksRef.current.onError?.("unknown");
      return false;
    }

    wantListenRef.current = true;

    if (holdingRef.current) {
      holdingRef.current = false;
      finalizeRecording();
      return false;
    }

    try {
      const stream = await openMicrophone();
      if (!wantListenRef.current) {
        stream.getTracks().forEach((track) => track.stop());
        return false;
      }

      const context = new AudioContext();
      if (context.state === "suspended") {
        await context.resume().catch(() => {});
      }

      const source = context.createMediaStreamSource(stream);
      const processor = context.createScriptProcessor(4096, 1, 1);
      const mute = context.createGain();
      mute.gain.value = 0;

      samplesRef.current = [];
      heardVoiceRef.current = false;
      silenceMsRef.current = 0;
      stoppingRef.current = false;
      const frameMs = (4096 / context.sampleRate) * 1000;

      processor.onaudioprocess = (event) => {
        if (!holdingRef.current || stoppingRef.current) {
          return;
        }

        const frame = new Float32Array(event.inputBuffer.getChannelData(0));
        samplesRef.current.push(frame);

        const energy = rmsOf(frame);
        const elapsed = Date.now() - startedAtRef.current;
        if (energy >= MIN_RMS) {
          heardVoiceRef.current = true;
          silenceMsRef.current = 0;
        } else if (heardVoiceRef.current) {
          silenceMsRef.current += frameMs;
        }

        const shouldStop =
          (heardVoiceRef.current && silenceMsRef.current >= SILENCE_STOP_MS) ||
          elapsed >= MAX_LISTEN_MS ||
          (!heardVoiceRef.current && elapsed >= NO_VOICE_MS);

        if (shouldStop) {
          stoppingRef.current = true;
          window.setTimeout(() => stopListeningRef.current(), 0);
        }
      };

      source.connect(processor);
      processor.connect(mute);
      mute.connect(context.destination);

      streamRef.current = stream;
      audioContextRef.current = context;
      processorRef.current = processor;
      sourceRef.current = source;
      startedAtRef.current = Date.now();
      holdingRef.current = true;
      setListening(true);
      return true;
    } catch (err) {
      wantListenRef.current = false;
      holdingRef.current = false;
      samplesRef.current = [];
      stopCaptureGraph();
      setListening(false);
      callbacksRef.current.onError?.(mapCaptureError(err));
      return false;
    }
  }, [finalizeRecording, stopCaptureGraph]);

  const stopListening = useCallback(() => {
    wantListenRef.current = false;
    if (!holdingRef.current) {
      return;
    }
    holdingRef.current = false;
    stoppingRef.current = true;
    finalizeRecording();
  }, [finalizeRecording]);

  stopListeningRef.current = stopListening;

  return {
    listening,
    processing,
    supported,
    startListening,
    stopListening,
  };
}
