// ── Mini AI Voice Interface ────────────────────────────────────────

const miniVoiceStatus = $("#mini-voice-status");
const miniVoiceSubtitle = $("#mini-voice-subtitle");
const miniAvatarContainer = $("#mini-avatar-container");
const miniPttBtn = $("#mini-ptt-btn");
let currentPersona = "friend";

// Audio Queue Manager for sequential playback
class AudioQueue {
  constructor() {
    this.queue = [];
    this.isPlaying = false;
    this.currentAudio = null;
  }

  push(sentence) {
    if (!sentence.trim()) return;
    this.queue.push(sentence);
    this.playNext();
  }

  async playNext() {
    if (this.isPlaying || this.queue.length === 0) return;
    this.isPlaying = true;
    const text = this.queue.shift();
    
    // Show what is being spoken
    miniVoiceSubtitle.textContent = text;
    miniVoiceSubtitle.hidden = false;
    miniAvatarContainer.classList.add("speaking");
    miniVoiceStatus.textContent = "Mini is speaking...";

    try {
      const res = await api("/v1/voice/speak", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text })
      });
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const audio = new Audio(url);
      this.currentAudio = audio;
      
      audio.onended = () => {
        miniAvatarContainer.classList.remove("speaking");
        this.currentAudio = null;
        this.isPlaying = false;
        URL.revokeObjectURL(url);
        if (this.queue.length === 0) {
          miniVoiceStatus.textContent = "Hold spacebar or button to talk";
          miniVoiceSubtitle.hidden = true;
        } else {
          this.playNext();
        }
      };
      
      audio.onerror = () => {
        console.error("Audio playback error");
        miniAvatarContainer.classList.remove("speaking");
        this.currentAudio = null;
        this.isPlaying = false;
        this.playNext();
      };

      await audio.play();
    } catch (e) {
      console.error("TTS fetch failed", e);
      miniAvatarContainer.classList.remove("speaking");
      this.isPlaying = false;
      this.playNext();
    }
  }

  stop() {
    this.queue = [];
    if (this.currentAudio) {
      this.currentAudio.pause();
      this.currentAudio = null;
    }
    this.isPlaying = false;
    miniAvatarContainer.classList.remove("speaking");
    miniVoiceStatus.textContent = "Hold spacebar or button to talk";
    miniVoiceSubtitle.hidden = true;
  }
}

const ttsQueue = new AudioQueue();

async function loadMini() {
  try {
    const p = await apiJson("/v1/mini/profile");
    currentPersona = p.persona;
    updateMiniPersonaUI(p.persona, p.persona_title);
  } catch (e) {
    console.error("Failed to load Mini profile:", e);
  }
}

function updateMiniPersonaUI(persona, title) {
  currentPersona = persona;
  $("#mini-persona-badge").textContent = title;
  
  document.querySelectorAll(".persona-btn").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.persona === persona);
  });

  const panel = $("#panel-mini");
  panel.className = "panel active " + (persona === "mentor" ? "mentor-theme" : "");
}

document.querySelectorAll(".persona-btn").forEach((btn) => {
  btn.addEventListener("click", async () => {
    const persona = btn.dataset.persona;
    if (persona === currentPersona) return;
    try {
      const p = await apiJson("/v1/mini/profile", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ persona }),
      });
      updateMiniPersonaUI(p.persona, p.persona_title);
      toast(`Switched to ${p.persona_title} persona.`);
    } catch (e) {
      toast("Failed to switch persona: " + e.message, true);
    }
  });
});

// Microphone and Voice Loop
let mediaRecorder = null;
let audioChunks = [];
let isRecording = false;

async function startRecording() {
  if (isRecording) return;
  
  // Stop current playback if interrupting
  ttsQueue.stop();
  
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    mediaRecorder = new MediaRecorder(stream);
    audioChunks = [];

    mediaRecorder.ondataavailable = (event) => {
      if (event.data.size > 0) audioChunks.push(event.data);
    };

    mediaRecorder.onstop = async () => {
      stream.getTracks().forEach(track => track.stop());
      const audioBlob = new Blob(audioChunks, { type: 'audio/webm' }); // Chrome often uses webm for audio
      await handleAudioSubmission(audioBlob);
    };

    mediaRecorder.start();
    isRecording = true;
    miniPttBtn.classList.add("active");
    miniVoiceStatus.textContent = "Listening...";
    miniVoiceSubtitle.hidden = true;
  } catch (e) {
    console.error("Microphone access denied or error:", e);
    toast("Microphone access denied. Please allow microphone permissions.", true);
  }
}

function stopRecording() {
  if (!isRecording || !mediaRecorder) return;
  isRecording = false;
  miniPttBtn.classList.remove("active");
  mediaRecorder.stop();
  miniVoiceStatus.textContent = "Processing...";
}

// Push to talk event listeners
miniPttBtn.addEventListener("mousedown", startRecording);
miniPttBtn.addEventListener("mouseup", stopRecording);
miniPttBtn.addEventListener("mouseleave", stopRecording);
miniPttBtn.addEventListener("touchstart", (e) => { e.preventDefault(); startRecording(); });
miniPttBtn.addEventListener("touchend", (e) => { e.preventDefault(); stopRecording(); });
miniPttBtn.addEventListener("touchcancel", (e) => { e.preventDefault(); stopRecording(); });

// Also allow spacebar when Mini panel is active
document.addEventListener("keydown", (e) => {
  if (e.code === "Space" && !e.repeat && $("#panel-mini").classList.contains("active") && document.activeElement.tagName !== "INPUT" && document.activeElement.tagName !== "TEXTAREA") {
    e.preventDefault();
    startRecording();
  }
});

document.addEventListener("keyup", (e) => {
  if (e.code === "Space" && $("#panel-mini").classList.contains("active") && document.activeElement.tagName !== "INPUT" && document.activeElement.tagName !== "TEXTAREA") {
    e.preventDefault();
    stopRecording();
  }
});

async function handleAudioSubmission(blob) {
  miniVoiceStatus.textContent = "Thinking...";
  
  try {
    // 1. Transcribe
    const formData = new FormData();
    // Use .webm or .wav depending on what the browser generated. Whisper handles both.
    formData.append("audio", blob, "voice.webm");
    
    const token = getKey();
    const sttRes = await fetch("/v1/voice/transcribe", {
      method: "POST",
      headers: { ...(token ? { Authorization: `Bearer ${token}` } : {}) },
      body: formData
    });
    
    if (!sttRes.ok) throw new Error("STT failed");
    const sttData = await sttRes.json();
    const transcript = sttData.text.trim();
    
    if (!transcript) {
      miniVoiceStatus.textContent = "Hold spacebar or button to talk";
      return;
    }

    miniVoiceSubtitle.textContent = `You: "${transcript}"`;
    miniVoiceSubtitle.hidden = false;
    
    // 2. Send to Mini Chat LLM via SSE
    const chatRes = await api("/v1/mini/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: transcript, persona: currentPersona }),
    });

    const reader = chatRes.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    let sentenceBuffer = "";

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });

      while (true) {
        let lfIdx = buf.indexOf("\n\n");
        let crlfIdx = buf.indexOf("\r\n\r\n");
        let idx = -1;
        let sepLen = 2;

        if (crlfIdx >= 0 && (lfIdx < 0 || crlfIdx < lfIdx)) {
          idx = crlfIdx;
          sepLen = 4;
        } else {
          idx = lfIdx;
          sepLen = 2;
        }

        if (idx < 0) break;

        const block = buf.slice(0, idx);
        buf = buf.slice(idx + sepLen);
        const { event, data } = parseSSE(block);

        if (event === "token") {
          sentenceBuffer += data;
          // Simple sentence boundary detection
          if (/[.!?]\s/.test(sentenceBuffer) || /[.!?]$/.test(data)) {
             const sentence = sentenceBuffer.trim();
             if (sentence) {
               ttsQueue.push(sentence);
             }
             sentenceBuffer = "";
          }
        }
      }
    }
    
    // Push any remaining text
    if (sentenceBuffer.trim()) {
      ttsQueue.push(sentenceBuffer.trim());
    }

  } catch (e) {
    console.error("Voice loop error:", e);
    toast("Voice error: " + e.message, true);
    miniVoiceStatus.textContent = "Hold spacebar or button to talk";
  }
}

// Memories panel (keep as before)
$("#mini-memories-btn").addEventListener("click", async () => {
  const panel = $("#mini-memories-panel");
  const list = $("#mini-memories-list");
  panel.hidden = false;
  list.innerHTML = `<div class="mini-memories-empty">Loading memories…</div>`;
  
  try {
    const data = await apiJson("/v1/mini/memories");
    list.innerHTML = "";
    if (data.memories && data.memories.length > 0) {
      data.memories.forEach((m) => {
        const card = el("div", "mini-memory-card");
        card.textContent = m.content;
        list.appendChild(card);
      });
    } else {
      list.innerHTML = `<div class="mini-memories-empty">No memories yet — start chatting and I'll remember the important stuff.</div>`;
    }
  } catch (e) {
    list.innerHTML = `<div class="mini-memories-empty bad">Failed to load: ${e.message}</div>`;
  }
});

$("#mini-memories-close").addEventListener("click", () => {
  $("#mini-memories-panel").hidden = true;
});

