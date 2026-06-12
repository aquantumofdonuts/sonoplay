/**
 * Shared utility functions for SonoPlay templates.
 */

/**
 * Escape HTML special characters to prevent XSS.
 * @param {string} value - The text to escape
 * @returns {string} The escaped text
 */
function escapeHtml(value) {
    if (value === null || value === undefined) return '';
    return String(value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}

/**
 * Format milliseconds as human-readable duration (e.g., "1:05:30" or "3:45").
 * @param {number} ms - Duration in milliseconds
 * @returns {string} Formatted duration string
 */
function formatDuration(ms) {
    if (!ms) return '0:00';
    const seconds = Math.floor((ms / 1000) % 60);
    const minutes = Math.floor((ms / (1000 * 60)) % 60);
    const hours = Math.floor((ms / (1000 * 60 * 60)));
    const secondsStr = seconds.toString().padStart(2, '0');
    if (hours > 0) {
        return `${hours}:${minutes.toString().padStart(2, '0')}:${secondsStr}`;
    }
    return `${minutes}:${secondsStr}`;
}

/**
 * Replace a grid's content wholesale (loading / empty states).
 * Clears the keyed-patching marker so the next patch starts fresh.
 */
function resetGrid(grid, html) {
    delete grid.dataset.keyed;
    grid.innerHTML = html;
}

/**
 * Patch a grid of keyed cards in place instead of rebuilding innerHTML.
 * Cards whose rendered HTML is unchanged are left untouched, preserving
 * hover/focus state and in-flight clicks during polling refreshes.
 *
 * @param {HTMLElement} grid - Container element
 * @param {Array} items - Data items to render
 * @param {function} getKey - item => stable string key
 * @param {function} getHtml - item => card HTML (single root element)
 */
function patchKeyedGrid(grid, items, getKey, getHtml) {
    if (!grid.dataset.keyed) {
        grid.innerHTML = '';
        grid.dataset.keyed = '1';
    }
    const existing = new Map();
    Array.from(grid.children).forEach(el => {
        if (el.dataset.key) existing.set(el.dataset.key, el);
    });
    const seen = new Set();
    items.forEach((item, i) => {
        const key = getKey(item);
        const html = getHtml(item);
        seen.add(key);
        let el = existing.get(key);
        if (!el || el.dataset.html !== html) {
            const tmp = document.createElement('div');
            tmp.innerHTML = html;
            const fresh = tmp.firstElementChild;
            fresh.dataset.key = key;
            fresh.dataset.html = html;
            if (el) {
                el.replaceWith(fresh);
            }
            el = fresh;
        }
        const ref = grid.children[i] || null;
        if (ref !== el) {
            grid.insertBefore(el, ref);
        }
    });
    Array.from(grid.children).forEach(el => {
        if (!el.dataset.key || !seen.has(el.dataset.key)) el.remove();
    });
}

/**
 * Shared Plex PIN link/relink flow (used by the nav status button, the
 * devices page, the groups page, and the onboarding wizard).
 *
 * @param {string} uuid - Device or group UUID
 * @param {Object} opts
 * @param {string} [opts.mode='link'] - 'link' (check status first) or 'relink' (force new PIN)
 * @param {string} [opts.subject='Device'] - Label used in dialog text
 * @returns {Promise<boolean>} true if the device ended up linked
 */
async function runPlexPinFlow(uuid, { mode = 'link', subject = 'Device' } = {}) {
    try {
        const formData = new FormData();
        formData.append('uuid', uuid);
        formData.append(mode === 'relink' ? 'relink' : 'check_status', 'true');

        const response = await fetch('/', { method: 'POST', body: formData });
        if (!response.ok) throw new Error('Network response was not ok');
        const data = await response.json();

        if (data.status === 'linked') {
            Swal.fire({
                icon: 'success',
                title: 'Linked!',
                text: `${subject} is successfully linked to Plex`,
                timer: 2000,
                showConfirmButton: false
            });
            return true;
        }
        if (!data.pin) return false;

        const result = await Swal.fire({
            icon: 'info',
            title: mode === 'relink' ? 'Relink to Plex' : 'Link to Plex',
            html: `
                <p>Visit <a href="https://plex.tv/link" target="_blank" style="color: #e5a00d; font-weight: bold;">plex.tv/link</a></p>
                <p style="margin-top: 1rem;">Enter this code:</p>
                <div style="font-size: 2rem; font-weight: bold; letter-spacing: 0.2rem; font-family: 'JetBrains Mono', monospace; margin: 1rem 0;">${escapeHtml(String(data.pin))}</div>
            `,
            confirmButtonText: "I've Entered the Code",
            showCancelButton: true,
            cancelButtonText: 'Cancel'
        });
        if (!result.isConfirmed) return false;

        Swal.fire({
            title: 'Checking...',
            allowOutsideClick: false,
            didOpen: () => Swal.showLoading()
        });
        const verifyForm = new FormData();
        verifyForm.append('uuid', uuid);
        verifyForm.append('pin_id', data.pin_id);
        const verifyResponse = await fetch('/', { method: 'POST', body: verifyForm });
        if (verifyResponse.ok) {
            const verifyData = await verifyResponse.json();
            if (verifyData.status === 'linked') {
                Swal.fire({
                    icon: 'success',
                    title: mode === 'relink' ? 'Relinked!' : 'Linked!',
                    text: `${subject} is now connected to Plex`,
                    timer: 2000,
                    showConfirmButton: false
                });
                return true;
            }
            Swal.fire({
                icon: 'warning',
                title: 'Not Yet Linked',
                text: 'Please enter the code at plex.tv/link and try again.'
            });
        }
        return false;
    } catch (error) {
        console.error('Plex link flow failed:', error);
        Swal.fire('Error', `Failed to ${mode === 'relink' ? 'relink' : 'link'} ${subject.toLowerCase()}`, 'error');
        return false;
    }
}

/**
 * Send a playback command to the real Plex player routes.
 * UI command names are mapped to the server's route names.
 */
async function sendPlaybackCommand(uuid, command) {
    const routeMap = {
        previous: 'skipPrevious',
        next: 'skipNext',
        pause: 'pause',
        play: 'play',
        stop: 'stop'
    };
    const route = routeMap[command] || command;
    const response = await fetch(`/player/playback/${route}?commandID=0&type=music`, {
        headers: {
            'X-Plex-Target-Client-Identifier': uuid,
            'X-Plex-Client-Identifier': 'sonoplay-web'
        }
    });
    if (!response.ok) throw new Error(`Command ${command} failed (${response.status})`);
}
