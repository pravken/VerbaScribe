// VerbaScribe page script, part 6 of 9: The player: transport, volume and voice enhancer (the wave: redesign_player.js).
// The parts are classic scripts sharing one global scope, loaded in order by index.html.

// The wave of the player is drawn by redesign_player.js (rpRender): these two names stay for the
// places that call them (opening a recording, its processing finishing, the video layout)
async function buildPlayerWaveformEnvelope(meeting) {
  await rpLoadMeeting(meeting);
}

function renderPlayerWaveform() {
  rpRender();
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
  if (slider) { slider.value = effectiveVol; slider.style.setProperty('--v', `${effectiveVol}%`); }
  if (label) label.textContent = `${effectiveVol}%`;
  if (iconOn && iconMuted) {
    iconOn.classList.toggle('hidden', effectiveVol === 0);
    iconMuted.classList.toggle('hidden', effectiveVol > 0);
  }
}

function cyclePlaybackSpeed() {
  state.speedIndex = (state.speedIndex + 1) % state.playbackSpeeds.length;
  rpSetSpeed(state.playbackSpeeds[state.speedIndex]);
}

// «Студийный голос» in «Звук»: the sound while listening only; the file is not changed
function toggleStudioVoiceEnhancer(quiet) {
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

  rpRenderSoundChip();
  if (quiet !== true) showToast(state.studioVoiceActive ? t('uiStudioVoiceEnabled') : t('uiOriginalAudio'));
}
