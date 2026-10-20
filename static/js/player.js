// VerbaScribe page script, part 6 of 9: The player: waveform, transport, volume and voice enhancer.
// The parts are classic scripts sharing one global scope, loaded in order by index.html.

// =========================================================================
// SOUNDCLOUD-STYLE WAVEFORM VISUALIZER FOR BOTTOM PLAYER BAR
// =========================================================================
async function buildPlayerWaveformEnvelope(meeting) {
  // Flat placeholder until the real loudness envelope of the recording is loaded
  state.playerPeaks = new Array(120).fill(0.14);
  renderPlayerWaveform(0);
  if (!meeting || !meeting.audio_path) return;
  try {
    const res = await fetch(`/api/meetings/${meeting.id}/peaks?bars=120`);
    if (!res.ok) return;
    const data = await res.json();
    if (!state.activeMeeting || state.activeMeeting.id !== meeting.id) return;
    if (Array.isArray(data.peaks) && data.peaks.length > 0) {
      state.playerPeaks = data.peaks.map(v => Math.max(0.08, Number(v) || 0));
      const player = document.getElementById('media-player');
      const dur = (player && player.duration) || meeting.duration || 1;
      renderPlayerWaveform(((player && player.currentTime) || 0) / dur);
    }
  } catch (e) {}
}

function renderPlayerWaveform(progressRatio = 0) {
  const canvas = document.getElementById('player-waveform-canvas');
  if (!canvas) return;
  const rect = canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  const w = Math.max(200, rect.width || 540);
  const h = Math.max(24, rect.height || 32);

  if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
  }

  const ctx = canvas.getContext('2d');
  ctx.save();
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, w, h);

  const peaks = state.playerPeaks.length ? state.playerPeaks : new Array(110).fill(0.25);
  const n = peaks.length;
  const gap = 2;
  const barW = Math.max(2, (w - gap * (n - 1)) / n);
  const centerY = h * 0.58;
  const maxTop = h * 0.50;
  const maxBot = h * 0.28;

  for (let i = 0; i < n; i++) {
    const x = i * (barW + gap);
    const barRatio = i / n;
    const amp = Math.max(0.10, Math.min(1.0, peaks[i]));
    const topH = Math.max(2, amp * maxTop);
    const botH = Math.max(1.5, amp * maxBot * 0.65);

    let topColor = 'rgba(255, 255, 255, 0.24)';
    let botColor = 'rgba(255, 255, 255, 0.11)';

    if (barRatio <= progressRatio) {
      topColor = '#0A84FF';
      botColor = 'rgba(10, 132, 255, 0.42)';
    } else if (state.playerHoverRatio >= 0 && barRatio <= state.playerHoverRatio) {
      topColor = 'rgba(10, 132, 255, 0.65)';
      botColor = 'rgba(10, 132, 255, 0.25)';
    }

    ctx.fillStyle = topColor;
    ctx.beginPath();
    ctx.roundRect(x, centerY - topH, barW, topH, 1.5);
    ctx.fill();

    ctx.fillStyle = botColor;
    ctx.beginPath();
    ctx.roundRect(x, centerY + 1.5, barW, botH, 1);
    ctx.fill();
  }

  ctx.restore();
}

// --- Player Transport, Volume Control & Studio Voice Enhancer ---
function togglePlayPause() {
  const player = document.getElementById('media-player');
  if (!player) return;
  if (player.paused) player.play();
  else player.pause();
}

function skipPlayer(deltaSec) {
  const player = document.getElementById('media-player');
  if (!player) return;
  player.currentTime = Math.max(0, player.currentTime + deltaSec);
}

function seekPlayer(seconds) {
  const player = document.getElementById('media-player');
  if (!player) return;
  clearTimeout(state.voicePreviewTimer);
  const target = Math.max(0, Number(seconds) || 0);
  // Until the file's metadata is loaded (just opened, or the audio track was switched) a seek
  // can be dropped and playback starts from 0: apply it once the player knows the duration
  if (player.readyState < 1) {
    player.addEventListener('loadedmetadata', () => { player.currentTime = target; player.play(); }, { once: true });
    return;
  }
  player.currentTime = target;
  player.play();
}

function changePlayerVolume(val) {
  const v = Math.max(0, Math.min(100, Number(val)));
  state.volume = v;
  state.muted = (v === 0);
  localStorage.setItem('verbascribe_volume', String(v));

  const player = document.getElementById('media-player');
  if (player) {
    player.muted = state.muted;
    player.volume = v / 100;
  }
  updateVolumeUI();
}

function toggleMutePlayer() {
  state.muted = !state.muted;
  const player = document.getElementById('media-player');
  if (player) {
    player.muted = state.muted;
    if (!state.muted && state.volume === 0) {
      state.volume = 80;
      player.volume = 0.8;
    }
  }
  updateVolumeUI();
}

function updateVolumeUI() {
  const slider = document.getElementById('player-volume-slider');
  const label = document.getElementById('player-volume-label');
  const iconOn = document.getElementById('icon-vol-on');
  const iconMuted = document.getElementById('icon-vol-muted');

  const effectiveVol = state.muted ? 0 : state.volume;
  if (slider) slider.value = effectiveVol;
  if (label) label.textContent = `${effectiveVol}%`;
  if (iconOn && iconMuted) {
    iconOn.classList.toggle('hidden', effectiveVol === 0);
    iconMuted.classList.toggle('hidden', effectiveVol > 0);
  }
}

function cyclePlaybackSpeed() {
  state.speedIndex = (state.speedIndex + 1) % state.playbackSpeeds.length;
  const rate = state.playbackSpeeds[state.speedIndex];
  const player = document.getElementById('media-player');
  if (player) player.playbackRate = rate;
  document.getElementById('speed-cycle-btn').textContent = `${rate.toFixed(rate % 1 === 0 ? 1 : 2)}x`;
}

function toggleStudioVoiceEnhancer() {
  const player = document.getElementById('media-player');
  if (!player) return;

  if (!window._audioCtx) {
    const AudioCtx = window.AudioContext || window.webkitAudioContext;
    const ctx = new AudioCtx();
    const source = ctx.createMediaElementSource(player);

    // 1. High-pass filter at 85 Hz (removes low rumble & mic hum)
    const hp = ctx.createBiquadFilter();
    hp.type = 'highpass';
    hp.frequency.value = 85;

    // 2. Presence boost around 3.1 kHz (speech clarity & intelligibility)
    const presence = ctx.createBiquadFilter();
    presence.type = 'peaking';
    presence.frequency.value = 3100;
    presence.Q.value = 1.0;
    presence.gain.value = 4.5;

    // 3. Low-pass filter at 7.8 kHz (removes high hiss)
    const lp = ctx.createBiquadFilter();
    lp.type = 'lowpass';
    lp.frequency.value = 7800;

    // 4. Broadcast compressor (levels quiet and loud voices)
    const comp = ctx.createDynamicsCompressor();
    comp.threshold.value = -26;
    comp.knee.value = 18;
    comp.ratio.value = 4.5;
    comp.attack.value = 0.005;
    comp.release.value = 0.18;

    const makeupGain = ctx.createGain();
    makeupGain.gain.value = 1.45;

    window._audioCtx = ctx;
    window._audioNodes = { source, hp, presence, lp, comp, makeupGain };
  }

  state.studioVoiceActive = !state.studioVoiceActive;
  const { source, hp, presence, lp, comp, makeupGain } = window._audioNodes;
  const ctx = window._audioCtx;
  if (ctx.state === 'suspended') ctx.resume();

  source.disconnect();
  hp.disconnect();
  presence.disconnect();
  lp.disconnect();
  comp.disconnect();
  makeupGain.disconnect();

  if (state.studioVoiceActive) {
    source.connect(hp);
    hp.connect(presence);
    presence.connect(lp);
    lp.connect(comp);
    comp.connect(makeupGain);
    makeupGain.connect(ctx.destination);
  } else {
    source.connect(ctx.destination);
  }

  const btn = document.getElementById('studio-voice-btn');
  if (btn) {
    btn.className = state.studioVoiceActive
      ? 'ui-btn ui-btn-active'
      : 'ui-btn ui-btn-subtle';
  }
  showToast(
    state.studioVoiceActive
      ? (state.uiLang === 'en' ? 'Studio Voice Enabled' : 'Студийный голос включён (шумодав + компрессор)')
      : (state.uiLang === 'en' ? 'Original Audio' : 'Исходный звук')
  );
}
