(() => {
  const form = document.querySelector('#create-form');
  const submitButton = document.querySelector('#submit-btn');
  const jobPanel = document.querySelector('#job-panel');
  const jobTitle = document.querySelector('#job-title');
  const jobStatus = document.querySelector('#job-status');
  const progressBar = document.querySelector('#progress-bar');
  const progressLabel = document.querySelector('#progress-label');
  const stageLabel = document.querySelector('#stage-label');
  const jobMessage = document.querySelector('#job-message');
  const resultsSection = document.querySelector('#results-section');
  const clipsList = document.querySelector('#clips-list');
  const clipCount = document.querySelector('#clip-count');
  const template = document.querySelector('#clip-template');
  let activeJobId = null;
  let timer = null;
  let lastRenderedSignature = '';

  const stageText = {
    queued: 'Queued', downloading: 'Downloading video', transcribing: 'Transcribing words',
    analyzing: 'Finding highlights', rendering: 'Rendering vertical clips',
    completed: 'Complete', failed: 'Failed', 're-rendering': 'Re-rendering edited clip'
  };
  const fmtTime = (sec) => {
    const s = Math.max(0, Math.floor(Number(sec) || 0));
    return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
  };
  const safe = (value) => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

  function showError(message) {
    jobPanel.classList.remove('hidden');
    jobStatus.textContent = 'Error';
    jobStatus.classList.add('error-text');
    jobMessage.textContent = message;
    jobMessage.classList.add('error-text');
  }

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (timer) clearInterval(timer);
    lastRenderedSignature = '';
    clipsList.innerHTML = '';
    resultsSection.classList.add('hidden');
    const body = {
      youtube_url: form.youtube_url.value.trim(),
      top_n: Number(form.top_n.value),
      language: form.language.value.trim() || null,
    };
    submitButton.disabled = true;
    submitButton.innerHTML = '<span>Starting…</span><span aria-hidden="true">◌</span>';
    jobPanel.classList.remove('hidden');
    jobStatus.textContent = 'Queued';
    jobStatus.classList.remove('error-text');
    jobMessage.classList.remove('error-text');
    jobTitle.textContent = 'Preparing your clips';
    progressBar.style.width = '0%';
    progressLabel.textContent = '0%';
    stageLabel.textContent = 'Queued';
    jobMessage.textContent = 'This can take a while on the first run while the Whisper model is loaded.';
    try {
      const response = await fetch('/jobs', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(body) });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Could not start the job.');
      activeJobId = data.id;
      await refreshJob();
      timer = setInterval(refreshJob, 2500);
    } catch (error) {
      showError(error.message || 'Something went wrong.');
      submitButton.disabled = false;
      submitButton.innerHTML = '<span>Try again</span><span aria-hidden="true">↗</span>';
    }
  });

  async function refreshJob() {
    if (!activeJobId) return;
    try {
      const response = await fetch(`/jobs/${activeJobId}`, {cache:'no-store'});
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Could not read job status.');
      const progress = Math.max(0, Math.min(100, Number(data.progress) || 0));
      progressBar.style.width = `${progress}%`;
      progressLabel.textContent = `${progress}%`;
      stageLabel.textContent = stageText[data.stage] || data.stage || 'Working';
      jobStatus.textContent = data.status || 'running';
      jobTitle.textContent = data.video_title || 'Preparing your clips';
      if (data.error) {
        jobMessage.textContent = data.error;
        jobMessage.classList.add('error-text');
      } else if (data.status === 'completed') {
        jobMessage.textContent = `Transcribed ${data.transcript_word_count || 0} words. Re-render edits without repeating AI analysis.`;
        jobMessage.classList.remove('error-text');
      } else {
        jobMessage.textContent = 'Downloading → transcribing → finding moments → rendering captions. Keep this tab open for live progress.';
        jobMessage.classList.remove('error-text');
      }
      renderClips(data.clips || []);
      const pendingRenders = (data.clips || []).some(c => ['queued','rendering'].includes(c.render_status));
      if (['completed','failed'].includes(data.status) && !pendingRenders) {
        if (timer) clearInterval(timer);
        timer = null;
        submitButton.disabled = false;
        submitButton.innerHTML = '<span>Make another batch</span><span aria-hidden="true">↗</span>';
      }
    } catch (error) {
      jobMessage.textContent = error.message || 'Status request failed.';
      jobMessage.classList.add('error-text');
    }
  }

  function renderClips(clips) {
    if (!clips.length) return;
    const signature = clips.map(c => `${c.id}:${c.render_status}:${c.download_url}:${c.start_seconds}:${c.end_seconds}`).join('|');
    // Rebuild only when clips/status/output URLs change; this keeps video playback from restarting on every poll.
    if (signature === lastRenderedSignature) return;
    lastRenderedSignature = signature;
    clipsList.innerHTML = '';
    resultsSection.classList.remove('hidden');
    clipCount.textContent = `${clips.length} clip${clips.length === 1 ? '' : 's'}`;
    clips.forEach((clip, index) => {
      const node = template.content.firstElementChild.cloneNode(true);
      node.dataset.clipId = clip.id;
      node.querySelector('.clip-number').textContent = `CLIP ${String(index + 1).padStart(2, '0')}`;
      node.querySelector('.score-chip').textContent = `${Math.round(Number(clip.score) || 0)} / 100`;
      node.querySelector('.clip-title').textContent = clip.title || 'Minecraft moment';
      node.querySelector('.clip-reason').textContent = clip.reason || clip.hook || '';
      node.querySelector('.clip-time').textContent = `${fmtTime(clip.start_seconds)} – ${fmtTime(clip.end_seconds)} · ${Math.round(clip.end_seconds - clip.start_seconds)} sec`;
      const status = node.querySelector('.clip-render-status');
      status.textContent = clip.render_status || 'queued';
      status.classList.toggle('error-text', clip.render_status === 'failed');
      const video = node.querySelector('video');
      const placeholder = node.querySelector('.clip-placeholder');
      if (clip.download_url) {
        video.src = clip.download_url;
        video.classList.remove('hidden');
        placeholder.classList.add('hidden');
        const link = node.querySelector('.download-link');
        link.href = clip.download_url;
        link.classList.remove('hidden');
      }
      const edit = node.querySelector('.edit-form');
      edit.elements.start.value = Number(clip.start_seconds).toFixed(1);
      edit.elements.end.value = Number(clip.end_seconds).toFixed(1);
      const settings = clip.settings || {};
      ['font_name','font_size','position','outline','highlight_color','crop_focus_x','crop_focus_y','trim_silence'].forEach(key => {
        const control = edit.elements[key];
        if (!control) return;
        if (control.type === 'checkbox') control.checked = Boolean(settings[key]);
        else if (settings[key] !== undefined) control.value = settings[key];
      });
      edit.addEventListener('submit', event => submitRender(event, clip, node));
      clipsList.appendChild(node);
    });
  }

  async function submitRender(event, clip, node) {
    event.preventDefault();
    const form = event.currentTarget;
    const button = form.querySelector('button');
    const number = name => Number(form.elements[name].value);
    const body = {
      start: number('start'), end: number('end'), font_name: form.elements.font_name.value,
      font_size: number('font_size'), position: form.elements.position.value, outline: number('outline'),
      highlight_color: form.elements.highlight_color.value, crop_focus_x: number('crop_focus_x'),
      crop_focus_y: number('crop_focus_y'), trim_silence: form.elements.trim_silence.checked,
    };
    if (body.end - body.start < 20 || body.end - body.start > 60) {
      alert('Clip duration must be between 20 and 60 seconds.'); return;
    }
    button.disabled = true;
    button.textContent = 'Queueing render…';
    try {
      const response = await fetch(`/jobs/${activeJobId}/clips/${clip.id}/render`, {
        method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(body)
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Could not queue re-render.');
      jobMessage.textContent = data.message;
      if (!timer) timer = setInterval(refreshJob, 2500);
      await refreshJob();
    } catch (error) {
      alert(error.message || 'Could not queue re-render.');
    } finally {
      button.disabled = false;
      button.textContent = 'Save & re-render ↻';
    }
  }
})();
