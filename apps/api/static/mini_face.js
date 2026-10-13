// ── Mini AI · Animated Face Engine (Retro Robot v2 with Idle-Life) ─────
// Public API:
//   miniFace.setEmotion(name) – sets a named emotion + locks it while speaking
//   miniFace.setSpeaking(bool)
//   miniFace.setVolume(0-1)   – drives lip sync
//   miniFace.poke()           – wakes or emotes when tapped

class MiniFace {
  constructor(svg) {
    this.svg    = svg;
    this._baseEmotion = 'idle'; // Emotion requested by API
    this.emotion = 'idle';      // Actual displayed emotion
    this.speaking = false;
    this.volume   = 0;
    
    // Idle Life State
    this.idleState = 'awake'; // awake, wandering, bored, asleep
    this.lastActiveAt = Date.now();
    this._gazeX = 0;
    this._gazeY = 0;
    this._targetGazeX = 0;
    this._targetGazeY = 0;
    
    // Animation timers
    this._bounceT = 0;      // laugh bounce timer
    this._breathT = 0;      // sleep breath timer

    // SVG elements
    this.lEye  = svg.querySelector('#f-leye');
    this.rEye  = svg.querySelector('#f-reye');
    this.mouth = svg.querySelector('#f-mouth');
    this.extra = svg.querySelector('#f-extra');

    this._scheduleBlink();
    this._scheduleIdleChecks();
    this._loop();
    this.setEmotion('idle');
    
    // Interaction
    this.svg.addEventListener('click', () => this.poke());
    
    // Expose emotion label on DOM for debugging
    this._label = document.getElementById('mini-emotion-label');
  }

  poke() {
    this._wakeUp();
    if (this.idleState === 'awake') {
      // Already awake -> random micro emote
      const emotes = ['happy', 'excited', 'laughing', 'surprised'];
      const pick = emotes[Math.floor(Math.random() * emotes.length)];
      this.setEmotion(pick);
      // Auto revert after a few seconds
      setTimeout(() => {
        if (!this.speaking) this.setEmotion('idle');
      }, 2500);
    }
  }

  _wakeUp() {
    this.lastActiveAt = Date.now();
    this.idleState = 'awake';
    this._targetGazeX = 0;
    this._targetGazeY = 0;
    if (this.emotion === 'asleep' || this.emotion === 'bored') {
      this.setEmotion('surprised');
      setTimeout(() => {
        if (!this.speaking && this.idleState === 'awake') this.setEmotion('idle');
      }, 1500);
    }
  }

  // ── Emotion presets ──────────────────────────────────────────────────
  setEmotion(emotion) {
    this._baseEmotion = emotion;
    this._applyEmotion(emotion);
  }

  _applyEmotion(emotion) {
    this.emotion = emotion;
    if (this._label) this._label.textContent = emotion;
    if (!this.lEye || !this.rEye) return;

    if (this.extra) this.extra.setAttribute('d', '');

    switch (emotion) {
      case 'happy':
        this.lEye.setAttribute('d', 'M 63,74 Q 75,58 87,74');
        this.rEye.setAttribute('d', 'M 113,74 Q 125,58 137,74');
        break;

      case 'laughing':
        this.lEye.setAttribute('d', 'M 63,62 L 70,70 L 63,78 M 87,62 L 80,70 L 87,78');
        this.rEye.setAttribute('d', 'M 113,62 L 120,70 L 113,78 M 137,62 L 130,70 L 137,78');
        if (this.extra) this.extra.setAttribute('d', 
          'M 55,48 L 59,44 M 145,48 L 141,44 M 58,55 L 54,52 M 142,55 L 146,52');
        break;

      case 'excited':
        this.lEye.setAttribute('d', 'M 87,62 L 75,70 L 87,78');
        this.rEye.setAttribute('d', 'M 113,62 L 125,70 L 113,78');
        if (this.extra) this.extra.setAttribute('d',
          'M 56,46 L 60,42 M 144,46 L 140,42 M 55,54 L 51,52 M 145,54 L 149,52');
        break;

      case 'sad':
        this.lEye.setAttribute('d', 'M 63,64 Q 75,72 87,64');
        this.rEye.setAttribute('d', 'M 113,64 Q 125,72 137,64');
        break;

      case 'surprised':
        this.lEye.setAttribute('d', 'M 75,62 A 10 10 0 1 1 75.01,62 Z');
        this.rEye.setAttribute('d', 'M 125,62 A 10 10 0 1 1 125.01,62 Z');
        break;

      case 'thinking':
        this.lEye.setAttribute('d', 'M 75,68 A 4 4 0 1 1 75.01,68 Z');
        this.rEye.setAttribute('d', 'M 125,64 A 8 8 0 1 1 125.01,64 Z');
        break;

      case 'listening':
        this.lEye.setAttribute('d', 'M 75,62 A 8 8 0 1 1 75.01,62 Z');
        this.rEye.setAttribute('d', 'M 125,62 A 8 8 0 1 1 125.01,62 Z');
        break;
        
      case 'bored':
        this.lEye.setAttribute('d', 'M 65,64 Q 75,70 85,64');
        this.rEye.setAttribute('d', 'M 115,64 Q 125,70 135,64');
        break;
        
      case 'asleep':
        this.lEye.setAttribute('d', 'M 63,69 L 87,69');
        this.rEye.setAttribute('d', 'M 113,69 L 137,69');
        if (this.extra) this.extra.setAttribute('d', 
          'M 130,30 L 140,30 L 130,40 L 140,40 M 145,15 L 150,15 L 145,20 L 150,20');
        break;

      default: // idle
        this.lEye.setAttribute('d', 'M 75,62 L 75,76');
        this.rEye.setAttribute('d', 'M 125,62 L 125,76');
        break;
    }
  }

  setSpeaking(val) {
    this.speaking = val;
    if (val) {
      this._wakeUp();
    } else {
      this._bounceT = 0; // reset laugh bounce
      this.lastActiveAt = Date.now();
    }
  }
  
  setVolume(v) { 
    this.volume = Math.max(0, Math.min(1, v)); 
    if (v > 0.1) this.lastActiveAt = Date.now();
  }

  // ── Idle Life Engine ─────────────────────────────────────────────────
  _scheduleIdleChecks() {
    this._idleTimer = setInterval(() => {
      if (this.speaking) return;
      
      const elapsed = (Date.now() - this.lastActiveAt) / 1000;
      
      // Keep user's explicitly requested baseEmotion unless it's just 'idle'
      // If we are sad or thinking, we shouldn't immediately start wandering.
      const isNeutral = (this._baseEmotion === 'idle' || this._baseEmotion === 'listening');
      
      if (!isNeutral && elapsed < 30) return; // Keep emotion for a while

      if (elapsed > 60) {
        if (this.idleState !== 'asleep') {
          this.idleState = 'asleep';
          this._applyEmotion('asleep');
          this._targetGazeX = 0;
          this._targetGazeY = 0;
        }
      } else if (elapsed > 30) {
        if (this.idleState !== 'bored') {
          this.idleState = 'bored';
          this._applyEmotion('bored');
          this._targetGazeX = 0;
          this._targetGazeY = 4; // Look down
        }
      } else if (elapsed > 10) {
        if (this.idleState !== 'wandering') {
          this.idleState = 'wandering';
          this._applyEmotion('idle');
        }
        // Randomly pick a gaze target
        if (Math.random() < 0.4) {
          this._targetGazeX = (Math.random() - 0.5) * 8; // -4 to +4
          this._targetGazeY = (Math.random() - 0.5) * 6; // -3 to +3
        }
      }
    }, 1000);
  }

  // ── Mouth shape (rAF driven) ─────────────────────────────────────────
  _mouthD() {
    const e    = this.emotion;
    const v    = this.speaking ? this.volume : 0;
    const open = v * 90;

    switch (e) {
      case 'happy':
        return open > 2
          ? `M 87,97 Q 100,${97 + open * 0.7} 113,97 Z`
          : `M 87,97 Q 100,108 113,97 Z`;

      case 'laughing': {
        const bounce = Math.abs(Math.sin(this._bounceT)) * 25;
        return `M 82,97 Q 100,${105 + bounce + open * 0.5} 118,97 Z`;
      }

      case 'excited':
        return open > 2
          ? `M 84,97 Q 100,${97 + open * 1.2} 116,97 Z`
          : `M 84,97 Q 100,114 116,97 Z`;

      case 'sad':
        return open > 2
          ? `M 87,103 Q 100,${93 - open * 0.5} 113,103`
          : `M 87,103 Q 100,91 113,103`;

      case 'surprised': {
        const ro = 5 + open * 0.3;
        return `M 100,94 A ${ro} ${ro} 0 1 1 100.01,94 Z`;
      }

      case 'thinking':
        return `M 88,99 Q 94,93 100,99 Q 106,105 113,99`;

      case 'listening':
        return open > 2
          ? `M 84,97 Q 100,${97 + open * 0.8} 116,97 Z`
          : `M 84,97 L 116,97`;
          
      case 'bored':
        return open > 2
          ? `M 90,100 Q 100,${100 + open * 0.5} 110,100 Z`
          : `M 90,100 Q 100,98 110,100`; // slightly downturned mouth
          
      case 'asleep': {
        // Sleep breathing opens mouth slightly rhythmically
        const breath = Math.sin(this._breathT) * 2;
        return `M 95,97 Q 100,${97 + Math.max(0, breath)} 105,97 Z`;
      }

      default: // idle
        return open > 2
          ? `M 89,97 Q 100,${97 + open * 0.8} 111,97 Z`
          : `M 93,97 Q 100,103 107,97`;
    }
  }

  // ── Digital blink ────────────────────────────────────────────────────
  _scheduleBlink() {
    const next = () => {
      const delay = 2500 + Math.random() * 3500;
      this._blinkTimer = setTimeout(() => {
        this._blink();
        // Double blink chance
        if (Math.random() < 0.25) setTimeout(() => this._blink(), 280);
        next();
      }, delay);
    };
    next();
  }

  _blink() {
    // Don't blink on laughing or asleep
    if (this.emotion === 'laughing' || this.emotion === 'asleep') return;
    if (!this.lEye || !this.rEye) return;

    const savedL = this.lEye.getAttribute('d');
    const savedR = this.rEye.getAttribute('d');

    this.lEye.setAttribute('d', 'M 62,69 L 88,69');
    this.rEye.setAttribute('d', 'M 112,69 L 138,69');

    setTimeout(() => {
      if (this.lEye.getAttribute('d') === 'M 62,69 L 88,69') {
        this.lEye.setAttribute('d', savedL);
        this.rEye.setAttribute('d', savedR);
      }
    }, 140);
  }

  // ── Main rAF loop ────────────────────────────────────────────────────
  _loop() {
    const tick = () => {
      if (this.mouth) {
        this.mouth.setAttribute('d', this._mouthD());
      }
      
      // Update gaze
      this._gazeX += (this._targetGazeX - this._gazeX) * 0.1;
      this._gazeY += (this._targetGazeY - this._gazeY) * 0.1;
      
      if (Math.abs(this._gazeX) < 0.01) this._gazeX = 0;
      if (Math.abs(this._gazeY) < 0.01) this._gazeY = 0;

      // Apply gaze to eyes
      const tform = `translate(${this._gazeX.toFixed(2)}, ${this._gazeY.toFixed(2)})`;
      if (this.lEye) this.lEye.setAttribute('transform', tform);
      if (this.rEye) this.rEye.setAttribute('transform', tform);

      // Advance breath/bounce animations
      if (this.emotion === 'laughing') {
        this._bounceT += 0.22;
        if (this.lEye && this.speaking) {
          const dy = Math.sin(this._bounceT * 1.5) * 3;
          const base = 70 + dy;
          this.lEye.setAttribute('d', `M 63,${base - 8} L 70,${base} L 63,${base + 8} M 87,${base - 8} L 80,${base} L 87,${base + 8}`);
          this.rEye.setAttribute('d', `M 113,${base - 8} L 120,${base} L 113,${base + 8} M 137,${base - 8} L 130,${base} L 137,${base + 8}`);
        }
      } else if (this.emotion === 'asleep') {
        this._breathT += 0.03; // Slow breathing rate
      }
      
      this._raf = requestAnimationFrame(tick);
    };
    tick();
  }

  destroy() {
    clearTimeout(this._blinkTimer);
    clearInterval(this._idleTimer);
    cancelAnimationFrame(this._raf);
  }
}

// Emotion is now driven by the backend's structured `emotion` event
// (see app.js → applyEmotion). The old keyword-regex detector was removed.

// ── Boot ─────────────────────────────────────────────────────────────
let miniFace = null;

function initMiniFace() {
  if (miniFace) return;
  const svg = document.querySelector('.mini-face-svg');
  if (!svg) { console.warn('[MiniFace] no SVG found'); return; }
  miniFace = new MiniFace(svg);
  console.log('[MiniFace] v2 initialized – emotion:', miniFace.emotion);
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', () => setTimeout(initMiniFace, 200));
} else {
  setTimeout(initMiniFace, 200);
}
