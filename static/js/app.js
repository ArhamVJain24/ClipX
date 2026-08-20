// ===== ClipX Results Page JavaScript =====

(function() {
    'use strict';

    // --- DOM Elements ---
    const video = document.getElementById('videoPlayer');
    const playBtn = document.getElementById('playBtn');
    const currentTimeEl = document.getElementById('currentTime');
    const totalTimeEl = document.getElementById('totalTime');
    const videoTitle = document.getElementById('videoTitle');
    const timelineTrack = document.getElementById('timelineTrack');
    const timelineWaveform = document.getElementById('timelineWaveform');
    const timelinePlayhead = document.getElementById('timelinePlayhead');
    const timelineCount = document.getElementById('timelineCount');
    const momentsList = document.getElementById('momentsList');

    // --- Load Data from Server ---
    const pathParts = window.location.pathname.split('/');
    const filename = decodeURIComponent(pathParts[pathParts.length - 1] || 'Unknown');
    let moments = [];
    let totalDuration = 0;

    // --- Utility: Format seconds to MM:SS or H:MM:SS ---
    function formatTime(seconds) {
        seconds = Math.floor(seconds);
        const h = Math.floor(seconds / 3600);
        const m = Math.floor((seconds % 3600) / 60);
        const s = seconds % 60;
        if (h > 0) {
            return h + ':' + String(m).padStart(2, '0') + ':' + String(s).padStart(2, '0');
        }
        return m + ':' + String(s).padStart(2, '0');
    }

    // --- Set Video Title ---
    if (videoTitle) {
        // Remove extension and clean up filename
        videoTitle.textContent = filename.replace(/\.mp4$/i, '').replace(/[_-]/g, ' ');
    }

    // --- Video Player Controls ---
    if (video && playBtn) {
        playBtn.addEventListener('click', () => {
            if (video.paused) {
                video.play();
                playBtn.style.opacity = '0';
            } else {
                video.pause();
                playBtn.style.opacity = '1';
            }
        });

        video.addEventListener('click', () => {
            if (video.paused) {
                video.play();
                playBtn.style.opacity = '0';
            } else {
                video.pause();
                playBtn.style.opacity = '1';
            }
        });

        video.addEventListener('pause', () => {
            playBtn.innerHTML = '<svg width="48" height="48" viewBox="0 0 24 24" fill="white"><polygon points="5,3 19,12 5,21"/></svg>';
            playBtn.style.opacity = '1';
        });

        video.addEventListener('play', () => {
            playBtn.innerHTML = '<svg width="48" height="48" viewBox="0 0 24 24" fill="white"><rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/></svg>';
            playBtn.style.opacity = '0';
        });
    }

    // --- Update Time Display ---
    if (video) {
        video.addEventListener('loadedmetadata', () => {
            totalTimeEl.textContent = formatTime(video.duration);
            
            // Re-render markers once we have the video duration if it was missing
            if (!totalDuration || totalDuration === 0) {
                timelineTrack.innerHTML = ''; // clear old markers
                if (timelineWaveform) timelineTrack.appendChild(timelineWaveform);
                if (timelinePlayhead) timelineTrack.appendChild(timelinePlayhead);
                renderTimelineMarkers();
            }
        });

        video.addEventListener('timeupdate', () => {
            currentTimeEl.textContent = formatTime(video.currentTime);

            // Update playhead position
            if (video.duration && timelinePlayhead) {
                const pct = (video.currentTime / video.duration) * 100;
                timelinePlayhead.style.left = pct + '%';
            }

            // Highlight active moment card
            highlightActiveMoment(video.currentTime);
        });
    }

    // --- Generate Waveform Bars ---
    if (timelineWaveform) {
        for (let i = 0; i < 200; i++) {
            const bar = document.createElement('div');
            bar.className = 'waveform-bar-mini';
            const height = Math.random() * 35 + 5;
            bar.style.height = height + 'px';
            bar.style.opacity = 0.15 + Math.random() * 0.2;
            timelineWaveform.appendChild(bar);
        }
    }

    // --- Render Timeline Markers ---
    function renderTimelineMarkers() {
        if (!timelineTrack) return;
        
        const durationToUse = (totalDuration && totalDuration > 0) ? totalDuration : video.duration;
        if (!durationToUse || durationToUse === 0) return; // Wait for metadata

        moments.forEach((m, index) => {
            const startPct = (m.start_seconds / durationToUse) * 100;
            const marker = document.createElement('div');
            marker.className = 'timeline-marker' + (m.has_reaction ? ' reaction' : ' insight');
            marker.style.left = startPct + '%';
            marker.title = m.quote;
            marker.addEventListener('click', () => seekToMoment(index));
            timelineTrack.appendChild(marker);
        });

        if (timelineCount) {
            timelineCount.textContent = moments.length + ' Flagged Highlights';
        }
    }

    // --- Render Moment Cards ---
    function renderMomentCards() {
        if (!momentsList) return;
        momentsList.innerHTML = '';

        moments.forEach((m, index) => {
            const card = document.createElement('div');
            card.className = 'moment-card';
            card.dataset.index = index;
            card.addEventListener('click', () => seekToMoment(index));

            // Determine score bar color
            let scoreColor = 'var(--color-muted)';
            if (m.score >= 70) scoreColor = 'var(--color-cyan)';
            else if (m.score >= 50) scoreColor = 'var(--color-amber)';

            // Build tags HTML
            const tagsHTML = (m.tags || []).map(tag => {
                const isReaction = tag.includes('REACTION') || tag.includes('EMOTIONAL') || tag.includes('HUMOR');
                return `<span class="tag ${isReaction ? 'tag-reaction' : 'tag-insight'}">${tag}</span>`;
            }).join('');

            // Build timestamp display
            const tsDisplay = m.timestamp_display || (formatTime(m.start_seconds) + ' — ' + formatTime(m.end_seconds));

            // Determine timestamp text color
            let timestampColor = '';
            if (m.score === 100) {
                timestampColor = 'color: var(--color-purple);';
            }

            card.innerHTML = `
                <div class="moment-top-row">
                    <span class="moment-timestamp" style="${timestampColor}">${tsDisplay}</span>
                    <div class="moment-score-group">
                        <div class="score-bar" style="background: ${scoreColor}"></div>
                        <span class="moment-score">${m.score}</span>
                    </div>
                </div>
                <p class="moment-quote">"${m.quote}"</p>
                <p class="moment-summary">${m.summary}</p>
                <div class="moment-tags">${tagsHTML}</div>
            `;

            // Staggered animation
            card.style.animationDelay = (index * 0.08) + 's';
            momentsList.appendChild(card);
        });
    }

    // --- Seek Video to a Moment ---
    function seekToMoment(index) {
        const m = moments[index];
        if (!m || !video) return;
        video.currentTime = m.start_seconds;
        video.play();
        playBtn.style.opacity = '0';

        // Scroll the card into view
        const card = momentsList.querySelector(`[data-index="${index}"]`);
        if (card) {
            card.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        }
    }

    // --- Highlight Active Moment Card ---
    function highlightActiveMoment(currentTime) {
        const cards = momentsList.querySelectorAll('.moment-card');
        cards.forEach((card, i) => {
            const m = moments[i];
            if (m && currentTime >= m.start_seconds && currentTime <= m.end_seconds) {
                card.classList.add('active');
            } else {
                card.classList.remove('active');
            }
        });
    }

    // --- Timeline Click to Seek ---
    if (timelineTrack && video) {
        timelineTrack.addEventListener('click', (e) => {
            const rect = timelineTrack.getBoundingClientRect();
            const x = e.clientX - rect.left;
            const pct = x / rect.width;
            video.currentTime = pct * video.duration;
        });
    }

    // --- Initialize ---
    fetch('/api/results/' + encodeURIComponent(filename))
        .then(res => res.json())
        .then(data => {
            if (data.success) {
                moments = data.moments;
                totalDuration = data.total_duration;
                renderTimelineMarkers();
                renderMomentCards();
            } else {
                if (momentsList) momentsList.innerHTML = `<p style="padding:20px;color:var(--color-amber);">Error: ${data.error}</p>`;
            }
        })
        .catch(err => {
            if (momentsList) momentsList.innerHTML = `<p style="padding:20px;color:var(--color-amber);">Failed to load results. Has this video been processed yet?</p>`;
        });

})();
