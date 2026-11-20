(() => {
	'use strict';

	let bridge = null;
	let navBridge = null;
	let updateData = null;
	let developerData = null;
	let developerLoading = false;
	let developerLoadFailed = false;
	let selectedSourceType = 'pr';
	let selectedTarget = '';
	let sourcePage = 0;
	let releasePage = 0;
	let selectedRelease = '';
	let latestRelease = null;
	let lostReleaseCommits = [];
	let isBleedingEdge = false;
	let branchCanUpdate = false;
	let gitPullAllowed = false;
	let spriteUpdateAvailable = false;
	let spriteCheckUpToDate = false;
	let selectedBranchView = 'main';
	let selectedDeveloperView = 'source';
	let selectedReleaseView = 'releases';
	let activeProgressKey = '';
	const busyOperations = new Set();
	let toastTimer = null;
	let modalArmTimer = null;
	// The open modal's opener and key handler, so a modal shown over another
	// one can take them over instead of stacking a second handler.
	let modalSession = null;
	// A pull request searched for by number that the loaded list lacks is
	// looked up on GitHub. For the rest of the session: the open ones found
	// (by number, oldest first), every other answer GitHub gave ('closed',
	// 'merged', 'not_found'), and the numbers still waiting for a reply. A
	// failed lookup (rate limit, offline) is kept for its search only, so
	// typing it again retries.
	const lookedUpPrs = new Map();
	const prLookupOutcomes = new Map();
	const prLookupsInFlight = new Set();
	let prLookupFailure = null;
	let prLookupTimer = null;
	const PR_LOOKUP_DELAY_MS = 350;
	const RATE_LIMIT_MESSAGE = 'Connection rate limited, pika try again soon!';
	const DROPDOWN_PAGE_SIZE = 50;
	const HIGH_RISK_UPDATE_MESSAGE = 'Update available (high risk)';
	const HIGH_RISK_ARM_DELAY_MS = 800;
	// 'checking' is the spinner shown until the first result; listing it here
	// means every re-render that clears the tones also clears it.
	const STATUS_TONES = ['checking', 'success', 'warning', 'error', 'rate-limit', 'neutral', 'high-risk'];
	// en-GB pins the "17 Nov 2026, 10:58" shape; the time zone is the viewer's.
	const SOURCE_DATE_FORMAT = new Intl.DateTimeFormat('en-GB', {
		day: 'numeric',
		month: 'short',
		year: 'numeric',
		hour: '2-digit',
		minute: '2-digit',
		hour12: false,
	});

	const byId = (id) => document.getElementById(id);
	const setText = (id, value) => { byId(id).textContent = value; };
	const setVisible = (element, visible) => element.classList.toggle('hidden', !visible);
	// Rendered at all: false for display:none on the element or any ancestor.
	const isShown = (element) => element.getClientRects().length > 0;

	// The selected sidebar tab is the current page; sub-tabs are toggle buttons.
	function markSidebarTab(button, active) {
		button.classList.toggle('active', active);
		if (active) button.setAttribute('aria-current', 'page');
		else button.removeAttribute('aria-current');
	}

	function markSubtab(button, active) {
		button.classList.toggle('active', active);
		button.setAttribute('aria-pressed', String(active));
	}

	// The wheel always scrolls the page; the keyboard moves between tabs.
	// Arrow keys step through one row (wrapping at the ends), Home/End jump to
	// its first/last tab, and hidden tabs are skipped. The tab is focused
	// before it is activated, so a modal it opens hands focus back to it.
	function wireTabKeys(container, selector, previousKey, nextKey) {
		container.addEventListener('keydown', (event) => {
			const current = event.target.closest(selector);
			if (!current || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
			const tabs = [...container.querySelectorAll(selector)]
				.filter((tab) => !tab.disabled && isShown(tab));
			const index = tabs.indexOf(current);
			if (index < 0) return;
			const target = {
				[previousKey]: tabs[(index - 1 + tabs.length) % tabs.length],
				[nextKey]: tabs[(index + 1) % tabs.length],
				Home: tabs[0],
				End: tabs[tabs.length - 1],
			}[event.key];
			if (!target) return;
			event.preventDefault();
			target.focus();
			if (target !== current || !target.classList.contains('active')) target.click();
		});
	}

	function showToast(message, isError = false) {
		const toast = byId('toast');
		toast.textContent = message;
		toast.classList.toggle('error', isError);
		toast.classList.add('visible');
		clearTimeout(toastTimer);
		toastTimer = setTimeout(() => toast.classList.remove('visible'), 3000);
	}

	// The controls in the modal card a Tab can land on right now; the delayed
	// confirm button joins once it is armed.
	function modalControls(card) {
		return [...card.querySelectorAll('a[href], button, input, select, textarea, [tabindex]')]
			.filter((element) => element.tabIndex >= 0 && !element.disabled && isShown(element));
	}

	// Back to the control that opened the modal; if it has since been
	// disabled, hidden or removed, to the open panel's active sub-tab, then
	// the active sidebar tab.
	function restoreFocusAfterModal(opener) {
		const canFocus = (element) => Boolean(element) &&
			element !== document.body &&
			element.isConnected &&
			!element.disabled &&
			typeof element.focus === 'function' &&
			isShown(element) &&
			!element.closest('[inert]');
		const panel = document.querySelector('.tab-panel.active');
		const target = [
			opener,
			panel && panel.querySelector('.branch-subtab.active'),
			document.querySelector('.update-tab-link.active'),
		].find(canFocus);
		if (target) target.focus();
	}

	// warningStyle: false (plain), true (warning) or 'danger'. armDelayMs keeps the
	// confirm button disabled briefly, so a double-click on the previous modal's
	// confirm button cannot fall through and confirm this one too.
	function showModal(title, message, confirmLabel, onConfirm, showCancel = true, warningStyle = false, escapeCloses = true, armDelayMs = 0) {
		const modal = byId('update-modal');
		// A modal shown while another is open replaces it: drop the old key
		// handler and keep the first opener, so focus still goes back there.
		let opener = document.activeElement;
		if (modalSession) {
			opener = modalSession.opener;
			modalSession.detach();
			modalSession = null;
		}
		if (opener && modal.contains(opener)) opener = null;
		setText('modal-title', title);
		const messageElement = byId('modal-message');
		messageElement.replaceChildren();
		if (message instanceof Node) messageElement.appendChild(message);
		else messageElement.textContent = message;
		setText('modal-confirm', confirmLabel);
		setVisible(byId('modal-cancel'), showCancel);
		modal.classList.remove('hidden');
		const modalCard = modal.querySelector('.update-modal-card');
		const tone = warningStyle === 'danger' ? 'danger' : warningStyle ? 'warning' : '';
		modalCard.classList.toggle('warning-modal', tone === 'warning');
		modalCard.classList.toggle('danger-modal', tone === 'danger');
		setVisible(byId('modal-icon'), Boolean(tone));
		const confirm = byId('modal-confirm');
		const cancel = byId('modal-cancel');
		confirm.classList.toggle('btn-danger', tone === 'danger');
		confirm.classList.toggle('btn-primary', tone !== 'danger');
		clearTimeout(modalArmTimer);
		confirm.disabled = armDelayMs > 0;
		if (armDelayMs > 0) {
			modalArmTimer = setTimeout(() => { confirm.disabled = false; }, armDelayMs);
		}
		// The page behind the modal is inert while it is open: no focus, no
		// clicks, hidden from assistive tech.
		const appContainer = document.querySelector('.app-container');
		const onKey = (event) => {
			if (event.key === 'Escape' && escapeCloses) {
				close();
				return;
			}
			if (event.key !== 'Tab') return;
			// Tab and Shift+Tab cycle through the card's controls only; a focus
			// that strayed outside the card (a click on the backdrop) comes back.
			event.preventDefault();
			const controls = modalControls(modalCard);
			if (!controls.length) return;
			const index = controls.indexOf(document.activeElement);
			const step = event.shiftKey ? -1 : 1;
			const next = index < 0
				? controls[event.shiftKey ? controls.length - 1 : 0]
				: controls[(index + step + controls.length) % controls.length];
			next.focus();
		};
		const close = () => {
			modal.classList.add('hidden');
			modalCard.classList.remove('warning-modal', 'danger-modal');
			clearTimeout(modalArmTimer);
			confirm.disabled = false;
			confirm.classList.remove('btn-danger');
			confirm.classList.add('btn-primary');
			confirm.onclick = null;
			cancel.onclick = null;
			document.removeEventListener('keydown', onKey);
			modalSession = null;
			// Leave inert first: nothing inside an inert page can take focus.
			if (appContainer) appContainer.removeAttribute('inert');
			restoreFocusAfterModal(opener);
		};
		confirm.onclick = () => {
			close();
			if (onConfirm) onConfirm();
		};
		cancel.onclick = close;
		document.addEventListener('keydown', onKey);
		modalSession = {
			opener,
			detach: () => document.removeEventListener('keydown', onKey),
		};
		if (appContainer) appContainer.setAttribute('inert', '');
		(showCancel ? cancel : confirm).focus();
	}

	function setCodeError(message, traceback) {
		setText('code-error-traceback', traceback || message || 'No traceback was provided.');
		byId('code-error').classList.remove('hidden');
	}

	function setOperationStatus(message, visible = true) {
		setText('operation-status', message || '');
		setVisible(byId('operation-status'), visible && Boolean(message));
	}

	function setProgressMessage(element, message, result = '') {
		const progressContainer = element.closest('.operation-progress, .sprite-progress');
		if (progressContainer) progressContainer.dataset.result = result;
		// Python sends some progress text with trailing dots; none are shown.
		message = String(message || '').replace(/\s*(?:\.{3}|\u2026)$/, '');
		element.replaceChildren();
		element.classList.remove(
			'progress-message-complete',
			'progress-message-failure',
			'progress-message-rate-limit',
			'progress-message-sprite-success',
			'progress-message-sprite-warning',
		);
		if (result === 'rate-limit') {
			element.classList.add('progress-message-rate-limit');
			element.textContent = message;
			return;
		}
		if (result === 'failure') {
			element.classList.add('progress-message-failure');
			element.textContent = message;
			return;
		}
		if (result === 'complete') {
			element.classList.add('progress-message-complete');
			const match = message.match(/^(Update complete\.)(.*)$/);
			if (match) {
				const heading = document.createElement('strong');
				heading.textContent = match[1];
				element.append(heading, document.createTextNode(match[2]));
				return;
			}
		}
		if (result === 'sprite-success') {
			element.classList.add('progress-message-sprite-success');
		} else if (result === 'sprite-warning') {
			element.classList.add('progress-message-sprite-warning');
		}
		element.textContent = message || '';
	}

	function activeProgressContainer() {
		if (!activeProgressKey) return null;
		const container = byId(`${activeProgressKey}-progress`);
		if (!container) throw new Error(`Missing progress container: ${activeProgressKey}`);
		return container;
	}

	function setActiveProgressMessage(message, result = '') {
		const container = activeProgressContainer();
		if (container) {
			setProgressMessage(
				container.querySelector('.progress-label span'),
				message,
				result,
			);
		}
	}

	function setOperationProgress(value, visible = true, indeterminate = false) {
		const container = activeProgressContainer();
		if (!container) return;
		const progress = container.querySelector('progress');
		if (indeterminate) {
			progress.removeAttribute('value');
			container.querySelector('output').textContent = '';
		} else {
			const percent = Math.max(0, Math.min(100, Number(value) || 0));
			progress.value = percent;
			container.querySelector('output').textContent = `${percent}%`;
		}
		setVisible(container, visible);
	}

	function setSpriteProgress(value, message = null, result = '') {
		const container = byId('sprites-progress');
		const progress = container.querySelector('progress');
		const percent = Math.max(0, Math.min(100, Number(value) || 0));
		progress.value = percent;
		container.querySelector('output').textContent = `${percent}%`;
		if (message !== null) {
			setProgressMessage(byId('sprites-status'), message, result);
		}
	}

	// The outcome of a sprite check: one status line and, when the update
	// changes anything, the New / Changed / Removed counts under it. The
	// progress block is for work in flight only, so it steps aside. An empty
	// message clears the outcome (a new check or an install has started).
	function setSpriteResult(message, tone = '', counts = null) {
		const statusLine = byId('sprites-result-status');
		statusLine.classList.remove(...STATUS_TONES);
		if (tone) statusLine.classList.add(tone);
		statusLine.textContent = message || '';
		const countList = byId('sprites-counts');
		let total = 0;
		countList.querySelectorAll('.sprite-count').forEach((item) => {
			const value = counts ? Number(counts[item.dataset.count]) || 0 : 0;
			total += value;
			item.querySelector('.sprite-count-value').textContent = String(value);
			item.classList.toggle('is-zero', value === 0);
		});
		setVisible(countList, total > 0);
		setVisible(byId('sprites-result'), Boolean(message));
		if (message) setVisible(byId('sprites-progress'), false);
	}

	function setDeveloperStatus(message) {
		setText('developer-status', message || '');
		setVisible(byId('developer-status'), Boolean(message));
	}

	// One format for every installed-source date, in the viewer's local time:
	// "17 Nov 2026, 10:58". Returns '' for a missing or unparseable value.
	function formatSourceDate(value) {
		if (value === null || value === undefined || value === '') return '';
		const date = new Date(value);
		return Number.isNaN(date.getTime()) ? '' : SOURCE_DATE_FORMAT.format(date);
	}

	// A missing value shows a plain muted placeholder instead.
	function setSourceDetail(id, value, placeholder) {
		const element = byId(id);
		element.textContent = value || placeholder;
		element.classList.toggle('source-detail-placeholder', !value);
	}

	function closeDropdown(dropdown) {
		dropdown.classList.remove('open');
		dropdown.querySelector('.custom-select-menu').classList.add('hidden');
		dropdown.querySelector('.custom-select-trigger').setAttribute('aria-expanded', 'false');
		const panel = dropdown.id === 'release-select'
			? byId('panel-releases')
			: dropdown.id === 'source-target'
				? byId('panel-developer')
				: null;
		if (panel) {
			panel.classList.remove('has-open-dropdown');
			panel.style.removeProperty('--dropdown-scroll-extra');
		}
		syncDropdownScrollSpace();
	}

	function syncDropdownScrollSpace() {
		const openDropdown = document.querySelector('.custom-select.open');
		const contentScroll = document.querySelector('.content-scroll');
		if (!openDropdown || !['release-select', 'source-target'].includes(openDropdown.id)) {
			return;
		}
		const panel = openDropdown.id === 'release-select'
			? byId('panel-releases')
			: byId('panel-developer');
		panel.classList.add('has-open-dropdown');
		const menu = openDropdown.querySelector('.custom-select-menu');
		const scrollBy = Math.max(
			0,
			menu.getBoundingClientRect().bottom -
				contentScroll.getBoundingClientRect().bottom + 24,
		);
		const currentExtra = Number.parseFloat(
			panel.style.getPropertyValue('--dropdown-scroll-extra'),
		) || 0;
		const requiredExtra = scrollBy;
		const extra = Math.max(currentExtra, Math.ceil(requiredExtra));
		panel.style.setProperty('--dropdown-scroll-extra', `${extra}px`);
		const maxScroll = contentScroll.scrollHeight - contentScroll.clientHeight;
		contentScroll.scrollTop = Math.min(contentScroll.scrollTop + scrollBy, maxScroll);
	}

	function closeDropdowns(except = null) {
		document.querySelectorAll('.custom-select.open').forEach((dropdown) => {
			if (dropdown !== except) closeDropdown(dropdown);
		});
	}

	function dropdownOptions(dropdown) {
		return [...dropdown.querySelectorAll('.custom-select-option')];
	}

	// The keyboard highlight (is-active) is remembered by value, so it
	// survives the list re-rendering on a search, a page change or a reload.
	function highlightOption(dropdown, option, scroll = true) {
		dropdownOptions(dropdown).forEach((item) => {
			item.classList.toggle('is-active', item === option);
		});
		dropdown.dataset.activeValue = option ? option.dataset.value : '';
		if (option && scroll) option.scrollIntoView({ block: 'nearest' });
	}

	function restoreHighlight(dropdown) {
		if (!dropdown.classList.contains('open')) return;
		const options = dropdownOptions(dropdown);
		const kept = options.find((option) => option.dataset.value === dropdown.dataset.activeValue);
		highlightOption(dropdown, kept || options[0] || null, false);
	}

	function wireDropdown(dropdown, onOpen = null) {
		const trigger = dropdown.querySelector('.custom-select-trigger');
		const menu = dropdown.querySelector('.custom-select-menu');
		const search = menu.querySelector('input[type="search"]');
		const openDropdown = () => {
			closeDropdowns(dropdown);
			dropdown.classList.add('open');
			menu.classList.remove('hidden');
			trigger.setAttribute('aria-expanded', 'true');
			const options = menu.querySelector('.custom-select-options');
			if (options) options.scrollTop = 0;
			syncDropdownScrollSpace();
			if (onOpen) onOpen();
			const choices = dropdownOptions(dropdown);
			highlightOption(
				dropdown,
				choices.find((option) => option.getAttribute('aria-selected') === 'true') ||
					choices[0] || null,
			);
			if (search && !search.classList.contains('hidden')) search.focus();
		};
		trigger.addEventListener('click', (event) => {
			event.stopPropagation();
			if (dropdown.classList.contains('open')) {
				closeDropdown(dropdown);
				return;
			}
			openDropdown();
		});
		// Closed: ArrowDown, Enter or Space on the trigger opens the menu.
		// Open: ArrowUp/ArrowDown move the highlight, Home/End jump to the ends,
		// Enter picks the highlighted option and Escape closes; both return
		// focus to the trigger. Typing (Space included) still goes to the search
		// box, and options and page buttons keep their own Enter.
		dropdown.addEventListener('keydown', (event) => {
			if (event.altKey || event.ctrlKey || event.metaKey) return;
			const onTrigger = event.target === trigger;
			if (!dropdown.classList.contains('open')) {
				if (onTrigger && ['ArrowDown', 'Enter', ' '].includes(event.key)) {
					event.preventDefault();
					openDropdown();
				}
				return;
			}
			const options = dropdownOptions(dropdown);
			const current = options.findIndex((option) => option.classList.contains('is-active'));
			let next = null;
			if (event.key === 'Escape') {
				event.preventDefault();
				closeDropdown(dropdown);
				trigger.focus();
				return;
			} else if (event.key === 'Enter' || event.key === ' ') {
				const picks = onTrigger || (event.key === 'Enter' && event.target === search);
				if (!picks) return;
				event.preventDefault();
				if (current >= 0) {
					options[current].click();
				} else if (onTrigger) {
					closeDropdown(dropdown);
				}
				return;
			} else if (event.key === 'ArrowDown') {
				next = options[Math.min(options.length - 1, current + 1)];
			} else if (event.key === 'ArrowUp') {
				next = options[Math.max(0, current - 1)];
			} else if ((event.key === 'Home' || event.key === 'End') && !event.shiftKey) {
				next = event.key === 'Home' ? options[0] : options[options.length - 1];
			} else {
				return;
			}
			event.preventDefault();
			if (!next) return;
			highlightOption(dropdown, next);
			// An option reached with Tab keeps the focus moving with the highlight.
			if (options.includes(event.target)) next.focus();
		});
		menu.addEventListener('focusin', (event) => {
			const option = event.target.closest('.custom-select-option');
			if (option) highlightOption(dropdown, option, false);
		});
		// Tabbing away closes the menu. A click on the menu's empty space moves
		// focus nowhere (no relatedTarget) and leaves it open.
		dropdown.addEventListener('focusout', (event) => {
			if (dropdown.classList.contains('open') && event.relatedTarget &&
				!dropdown.contains(event.relatedTarget)) {
				closeDropdown(dropdown);
			}
		});
		menu.addEventListener('click', (event) => {
			event.stopPropagation();
			// A page button that just became disabled has dropped focus; keep it
			// in the menu so the keyboard still works there.
			const pageButton = event.target.closest('.dropdown-page-controls button');
			if (pageButton && pageButton.disabled && search) search.focus();
		});
		menu.addEventListener('wheel', (event) => {
			event.stopPropagation();
			const options = menu.querySelector('.custom-select-options');
			if (!options || !options.contains(event.target)) {
				event.preventDefault();
				return;
			}
			const maxScroll = options.scrollHeight - options.clientHeight;
			const atStart = options.scrollTop <= 0;
			const atEnd = options.scrollTop >= maxScroll - 1;
			if (maxScroll <= 0 || (event.deltaY < 0 && atStart) ||
				(event.deltaY > 0 && atEnd)) {
				event.preventDefault();
			}
		}, { passive: false });
	}

	function setDropdownLabel(dropdownId, label) {
		byId(dropdownId).querySelector('.custom-select-value').textContent =
			label || 'Select an option';
	}

	function renderOptions(dropdownId, entries, selectedValue, emptyMessage, onSelect) {
		const dropdown = byId(dropdownId);
		const options = dropdown.querySelector('.custom-select-options');
		options.replaceChildren();
		if (!entries.length) {
			const empty = document.createElement('div');
			empty.className = 'custom-select-empty';
			empty.textContent = emptyMessage;
			options.appendChild(empty);
			return;
		}
		entries.forEach((entry) => {
			const option = document.createElement('button');
			option.type = 'button';
			option.className = 'custom-select-option';
			option.setAttribute('role', 'option');
			option.setAttribute('aria-selected', String(entry.value === selectedValue));
			option.dataset.value = entry.value;
			option.textContent = entry.label;
			option.addEventListener('click', () => {
				// The list is not re-rendered on a pick; mark the new choice now
				// so the menu reopens on it.
				options.querySelectorAll('.custom-select-option').forEach((item) => {
					item.setAttribute('aria-selected', String(item === option));
				});
				onSelect(entry);
				// A pick hands focus back to the dropdown it was made in.
				dropdown.querySelector('.custom-select-trigger').focus();
			});
			options.appendChild(option);
		});
		restoreHighlight(dropdown);
	}

	function currentBusy() {
		return busyOperations.size > 0;
	}

	function shouldSwitchToLatestRelease() {
		if (isBleedingEdge) return true;
		if (!updateData) return false;
		if (updateData.source_type === 'pr') return true;
		if (updateData.source_type === 'branch' && updateData.source_name !== 'main') {
			return true;
		}
		return Boolean(
			updateData.source_type === 'tag' &&
			latestRelease &&
			updateData.source_name !== latestRelease.name,
		);
	}

	function updateButtonStates() {
		const busy = currentBusy();
		const switchToRelease = shouldSwitchToLatestRelease();
		byId('branch-update-btn').disabled = busy || !branchCanUpdate;
		byId('git-pull-btn').disabled = busy || !gitPullAllowed;
		byId('latest-release-btn').disabled = busy || !latestRelease ||
			(latestRelease.name === (updateData && updateData.version) && !switchToRelease);
		byId('release-install-btn').disabled = busy || !selectedRelease;
		byId('developer-install-btn').disabled = busy || !selectedTarget;
		byId('sprites-check-btn').disabled = busy || spriteCheckUpToDate;
		byId('sprites-install-btn').disabled = busy || !spriteUpdateAvailable;
	}

	function renderBranch(data) {
		const state = data.state || {};
		const gitInfo = data.git_info || {};
		const active = data.active_branch || 'main';
		const localSha = typeof data.local_sha === 'string' ? data.local_sha : '';
		const hasBranchViews = Boolean(data.show_branch_views);
		const branchView = selectedBranchView;
		const viewData = branchView === 'main'
			? {
				localSha: data.main_commit_sha || '',
				remoteSha: data.main_remote_sha || '',
				error: data.main_error,
				commits: data.main_commits || [],
				targetName: 'main',
			}
			: {
				localSha,
				remoteSha: data.remote_sha || '',
				error: data.branch_error,
				commits: data.current_commits || [],
				targetName: data.query_branch || active,
			};
		const remoteSha = viewData.remoteSha;
		const compareSha = viewData.localSha;
		const relation = data.relation || state.git_relation || null;
		const developerSubtabs = byId('developer-subtabs');
		developerSubtabs.querySelectorAll('[data-developer-view="main"], [data-developer-view="current"]')
			.forEach((button) => {
				setVisible(button, button.dataset.developerView === 'main' || hasBranchViews);
			});
		if (!hasBranchViews && selectedDeveloperView !== 'source') {
			switchDeveloperView('source');
		}
		developerSubtabs.querySelectorAll('.branch-subtab').forEach((button) => {
			markSubtab(button, button.dataset.developerView === selectedDeveloperView);
		});
		const activeBranchTitle = byId('active-branch-title');
		activeBranchTitle.replaceChildren();
		activeBranchTitle.appendChild(document.createTextNode('Installed source: '));
		// Users know the main branch as "the pre-release"; the link still opens
		// the real branch. A Git checkout keeps the actual branch name.
		// Only a recorded install counts: with no update state (an AnkiWeb
		// release install) Python fills in branch/main as a default.
		const recordedSource = data.state || {};
		const preReleaseSource = !data.git_clone && recordedSource.source_type === 'branch' &&
			(recordedSource.source_name || 'main') === 'main';
		if (preReleaseSource || (data.source_type && data.source_name)) {
			const sourceLink = document.createElement('a');
			sourceLink.href = '#';
			sourceLink.className = 'source-link';
			sourceLink.textContent = preReleaseSource
				? 'Pre-release'
				: sourceLinkLabel(data.source_type, data.source_name);
			sourceLink.addEventListener('click', (event) => {
				event.preventDefault();
				openGitHubTarget(
					sourceUrl(data.source_type, data.source_name),
					'Could not open the installed source in your browser.',
				);
			});
			activeBranchTitle.appendChild(sourceLink);
		} else {
			activeBranchTitle.appendChild(document.createTextNode(active));
		}
		const mainCommitSha = data.main_commit_sha || (data.source_type === 'branch' &&
			data.source_name === 'main' ? localSha : '');
		const branchUpdateView = byId('branch-update-view');
		const branchUpdateParent = branchUpdateView.parentElement;
		const inPreRelease = branchUpdateParent.id === 'release-view-pre-release';
		const inDeveloperMain = Boolean(
			branchUpdateParent.closest('#panel-developer') &&
			branchUpdateParent.id === 'developer-view-main',
		);
		setText('branch-updates-title', inDeveloperMain
			? 'Recent pre-release changes'
			: inPreRelease ? 'Recent changes' : 'Recent branch changes');
		setVisible(
			byId('branch-update-description'),
			inPreRelease,
		);
		// Installed row, plus a Pre-release row whenever the branch views are on.
		setSourceDetail('installed-commit', localSha.slice(0, 7), 'Unknown');
		setSourceDetail(
			'commit-date',
			typeof data.commit_date === 'string' ? formatSourceDate(data.commit_date) : '',
			'Unknown',
		);
		setVisible(byId('pre-release-source-row'), hasBranchViews);
		setSourceDetail(
			'pre-release-commit',
			hasBranchViews ? mainCommitSha.slice(0, 7) : '',
			'Unknown',
		);
		setSourceDetail(
			'pre-release-commit-date',
			hasBranchViews && typeof data.main_commit_date === 'string'
				? formatSourceDate(data.main_commit_date)
				: '',
			'Unknown',
		);
		setSourceDetail(
			'last-update-installed',
			Number(data.installed_at) > 0
				? formatSourceDate(Number(data.installed_at) * 1000)
				: '',
			'Never',
		);

		const status = byId('branch-status');
		status.classList.remove(...STATUS_TONES);
		branchCanUpdate = false;
		let mainUpdateAvailable = false;
		// The action the button offers; empty hides the button. A state the
		// user cannot act on is explained by the status line, never by a
		// disabled button relabelled to describe it.
		const updateAction = branchView === 'main'
			? 'Update to pre-release'
			: 'Update installed source';
		let branchAction = '';
		// Why this Git checkout cannot be updated in place, as a full sentence.
		let checkoutBlocked = '';
		// Everything except a stable release is unreleased code.
		const highRiskView = branchView === 'main' ||
			isHighRiskTarget({ type: data.source_type, name: data.source_name });
		if (branchView === 'main' && hasBranchViews && !mainCommitSha) {
			if (remoteSha) {
				status.textContent = '';
			} else if (data.main_error === 'rate_limited') {
				status.textContent = RATE_LIMIT_MESSAGE;
				status.classList.add('rate-limit');
			} else if (data.main_error === 'connection') {
				status.textContent = 'Could not reach GitHub. Check your connection and try again.';
				status.classList.add('error');
			} else if (data.main_error === 'not_found') {
				status.textContent = 'Could not find the pre-release on GitHub.';
				status.classList.add('warning');
			} else {
				status.textContent = 'GitHub could not verify the pre-release. Please try again later.';
				status.classList.add('warning');
			}
			branchAction = updateAction;
			branchCanUpdate = Boolean(remoteSha);
		} else if (data.git_clone && active === 'detached') {
			checkoutBlocked = 'Your checkout is detached, so there is no branch to update. Pick a branch, release, tag, or PR from the other tabs.';
			status.textContent = checkoutBlocked;
			status.classList.add('warning');
		} else if (data.git_clone && gitInfo.dirty) {
			checkoutBlocked = 'Your checkout has local changes. Commit, stash, or discard them before updating.';
			status.textContent = checkoutBlocked;
			status.classList.add('warning');
		} else if (!remoteSha) {
			if (viewData.error === 'rate_limited') {
				status.textContent = RATE_LIMIT_MESSAGE;
				status.classList.add('rate-limit');
			} else if (viewData.error === 'not_found') {
				if (branchView === 'main') {
					status.textContent = 'Could not find the pre-release on GitHub.';
				} else if (data.source_type === 'pr') {
					status.textContent = `Could not find pull request #${data.source_name} on GitHub.`;
				} else if (data.source_type === 'tag' || data.source_type === 'release') {
					status.textContent = `Could not find ${data.source_type} '${viewData.targetName}' on GitHub.`;
				} else {
					status.textContent = `Could not find branch '${viewData.targetName}' on GitHub.`;
				}
				status.classList.add('warning');
			} else if (viewData.error === 'connection') {
				status.textContent = 'Could not reach GitHub. Check your connection and try again.';
				status.classList.add('error');
			} else if (viewData.error === 'api_error') {
				status.textContent = 'GitHub could not verify this source. Please try again later.';
				status.classList.add('warning');
			} else {
				status.textContent = 'Could not check connection.';
				status.classList.add('error');
			}
			// A Git checkout with no copy on the Ankimon repository has nothing
			// to follow; the status line above already says why.
			branchAction = data.git_clone ? '' : updateAction;
		} else if (data.git_clone && compareSha !== remoteSha && ['behind', 'diverged'].includes(relation)) {
			checkoutBlocked = relation === 'behind'
				? `Your checkout is ahead of '${active}' on the Ankimon repository, so it cannot be fast-forwarded.`
				: `Your checkout has diverged from '${active}' on the Ankimon repository, so it cannot be fast-forwarded.`;
			status.textContent = checkoutBlocked;
			status.classList.add('warning');
		} else if (compareSha !== remoteSha) {
			status.textContent = highRiskView
				? `${HIGH_RISK_UPDATE_MESSAGE} · Latest: ${remoteSha.slice(0, 7)}`
				: `Update available (${remoteSha.slice(0, 7)})`;
			status.classList.add(highRiskView ? 'high-risk' : 'warning');
			branchAction = updateAction;
			branchCanUpdate = true;
		} else {
			const noMainUpdatesForExternalSource = branchView === 'main' &&
				hasBranchViews && data.source_type &&
				!(data.source_type === 'branch' && data.source_name === 'main') &&
				viewData.commits.length === 0;
			if (noMainUpdatesForExternalSource) {
				status.textContent = 'Up to date';
				status.classList.add('success');
				branchAction = 'Switch to pre-release';
				branchCanUpdate = Boolean(remoteSha);
			} else {
				// Nothing to install: "Up to date" is the reason the button is hidden.
				status.textContent = 'Up to date';
				status.classList.add('success');
			}
		}

		if (branchView === 'main') {
			status.classList.remove(...STATUS_TONES);
			const requestError = data.main_error === 'rate_limited'
				? data.main_error
				: data.main_commits_error;
			if (requestError === 'rate_limited') {
				status.textContent = RATE_LIMIT_MESSAGE;
				status.classList.add('rate-limit');
			} else if (!remoteSha) {
				if (data.main_error === 'connection') {
					status.textContent = 'Could not reach GitHub. Check your connection and try again.';
					status.classList.add('error');
				} else if (data.main_error === 'not_found') {
					status.textContent = 'Could not find the pre-release on GitHub.';
					status.classList.add('warning');
				} else {
					status.textContent = 'GitHub could not verify the pre-release. Please try again later.';
					status.classList.add('warning');
				}
			} else if (data.main_commits_error) {
				status.textContent = data.main_commits_error === 'connection'
					? 'Could not reach GitHub. Check your connection and try again.'
					: 'GitHub could not load pre-release changes. Please try again later.';
				status.classList.add(data.main_commits_error === 'connection' ? 'error' : 'warning');
			} else if (checkoutBlocked && (viewData.commits.length || compareSha !== remoteSha)) {
				// An update is waiting but this checkout cannot take it. The
				// button is hidden, so the status line must keep the reason.
				status.textContent = checkoutBlocked;
				status.classList.add('warning');
			} else if (viewData.commits.length) {
				status.textContent = HIGH_RISK_UPDATE_MESSAGE;
				status.classList.add('high-risk');
				mainUpdateAvailable = true;
			} else {
				status.textContent = 'Up to date';
				status.classList.add('success');
			}
		}
		const externalPrOrBranch = hasBranchViews && (
			data.source_type === 'pr' ||
			(data.source_type === 'branch' && data.source_name !== 'main')
		);
		if (branchView === 'main' && externalPrOrBranch && remoteSha) {
			if (compareSha === remoteSha) {
				status.textContent = 'Up to date';
				status.classList.remove(...STATUS_TONES);
				status.classList.add('success');
				mainUpdateAvailable = false;
				branchAction = 'Switch to pre-release';
			}
			branchCanUpdate = true;
		}
		if (inPreRelease && branchView === 'main' && branchCanUpdate && mainUpdateAvailable) {
			branchAction = 'Update now';
		}
		// An action that can run always has a visible, named button.
		if (branchCanUpdate && !branchAction) branchAction = updateAction;
		const branchButton = byId('branch-update-btn');
		if (branchAction) branchButton.textContent = branchAction;
		setVisible(branchButton, Boolean(branchAction));

		const commits = byId('recent-commits');
		commits.replaceChildren();
		if (viewData.commits.length) {
			viewData.commits.forEach((commit) => {
				const item = document.createElement('li');
				const sha = String(commit.sha || '');
				if (/^[\da-f]{7,40}$/i.test(sha)) {
					const link = document.createElement('a');
					link.href = '#';
					link.className = 'commit-link';
					link.textContent = sha;
					link.addEventListener('click', (event) => {
						event.preventDefault();
						openGitHubTarget(
							`https://github.com/h0tp-ftw/ankimon/commit/${encodeURIComponent(sha)}`,
							'Could not open the commit in your browser.',
						);
					});
					item.append(link, document.createTextNode(commit.message || ''));
				} else {
					const code = document.createElement('code');
					code.textContent = sha;
					item.append(code, document.createTextNode(commit.message || ''));
				}
				commits.appendChild(item);
			});
		} else {
			const item = document.createElement('li');
			item.className = 'commit-list-empty';
			item.textContent = 'No new commit messages fetched.';
			commits.appendChild(item);
		}

		const skipUntil = Number(state.skip_until);
		byId('branch-snooze').checked = Number.isFinite(skipUntil) &&
			skipUntil > Date.now() / 1000;
		gitPullAllowed = Boolean(
			data.git_clone &&
			gitInfo.branch &&
			gitInfo.branch !== 'HEAD' &&
			!gitInfo.dirty &&
			remoteSha &&
			!['behind', 'diverged'].includes(relation),
		);
	}

	function switchDeveloperView(view) {
		selectedDeveloperView = ['main', 'current'].includes(view) ? view : 'source';
		byId('developer-subtabs').querySelectorAll('.branch-subtab').forEach((button) => {
			markSubtab(button, button.dataset.developerView === selectedDeveloperView);
		});
		document.querySelectorAll('#panel-developer .subtab-panel').forEach((panel) => {
			panel.classList.toggle('active', panel.id === `developer-view-${selectedDeveloperView}`);
		});
		setText('branch-updates-title', selectedDeveloperView === 'main'
			? 'Recent pre-release changes'
			: 'Recent branch changes');
		setVisible(byId('branch-update-description'), false);
		if (selectedDeveloperView !== 'source') {
			const viewPanel = byId(`developer-view-${selectedDeveloperView}`);
			viewPanel.appendChild(byId('branch-update-view'));
			const progressKey = selectedDeveloperView === 'main'
				? 'developer-main'
				: 'developer-current';
			viewPanel.appendChild(byId(`${progressKey}-progress`));
			if (updateData && updateData.show_branch_views) {
				selectedBranchView = selectedDeveloperView;
				renderBranch(updateData);
			}
		}
		updateButtonStates();
	}

	function switchReleaseView(view) {
		selectedReleaseView = view === 'pre-release' ? 'pre-release' : 'releases';
		byId('release-subtabs').querySelectorAll('.branch-subtab').forEach((button) => {
			markSubtab(button, button.dataset.releaseView === selectedReleaseView);
		});
		document.querySelectorAll('#panel-releases .subtab-panel').forEach((panel) => {
			panel.classList.toggle('active', panel.id === `release-view-${selectedReleaseView}`);
		});
		if (selectedReleaseView === 'pre-release') {
			const preReleasePanel = byId('release-view-pre-release');
			preReleasePanel.appendChild(byId('branch-update-view'));
			preReleasePanel.appendChild(byId('pre-release-progress'));
			selectedBranchView = 'main';
			setText('branch-updates-title', 'Recent changes');
			setVisible(byId('branch-update-description'), true);
			if (updateData) renderBranch(updateData);
		}
		// The branch button is shared; its enabled state follows the view
		// just rendered, not the one it was last shown in.
		updateButtonStates();
	}

	function showDeveloperWarningIfNeeded(force = false) {
		if (!bridge) return;
		bridge.getDeveloperWarningHidden((hidden) => {
			if (hidden && !force) return;
			const content = document.createElement('div');
			const warning = document.createElement('p');
			warning.className = 'warning-text modal-warning-text developer-warning-text';
			warning.append(
				document.createTextNode(
					'This section contains developer tools and is usually for testing purposes. Installations made here are ',
				),
			);
			const irreversible = document.createElement('strong');
			irreversible.textContent = 'irreversible';
			warning.append(
				irreversible,
				document.createTextNode(
					', and the code might not do as their description says. Pull requests and side branches may be experiments or work in progress. ',
				),
			);
			const trustSources = document.createElement('strong');
			trustSources.textContent = 'Install only from trusted sources.';
			warning.appendChild(trustSources);
			const row = document.createElement('label');
			row.className = 'check-row developer-warning-checkbox';
			const checkbox = document.createElement('input');
			checkbox.className = 'custom-checkbox';
			checkbox.type = 'checkbox';
			checkbox.checked = Boolean(hidden);
			const indicator = document.createElement('span');
			indicator.className = 'checkbox-indicator';
			indicator.setAttribute('aria-hidden', 'true');
			const label = document.createElement('span');
			label.textContent = 'Do not show this again';
			row.append(checkbox, indicator, label);
			content.append(warning, row);
			showModal(
				'Developer warning',
				content,
				'Close',
				() => bridge.setDeveloperWarningHidden(checkbox.checked, (saved) => {
					if (!saved) showToast('Could not save the developer warning setting.', true);
				}),
				false,
				true,
				false,
			);
		});
	}

	function renderReleases(releases, error = null) {
		const list = Array.isArray(releases) ? releases : [];
		latestRelease = list[0] || null;
		const latestLabel = byId('latest-release-label');
		latestLabel.classList.remove(...STATUS_TONES);
		isBleedingEdge = Boolean(updateData && updateData.bleeding_edge_release);
		lostReleaseCommits = updateData && Array.isArray(updateData.release_lost_commits)
			? updateData.release_lost_commits
			: [];
		if (error === 'rate_limited') {
			latestLabel.textContent = RATE_LIMIT_MESSAGE;
			latestLabel.classList.add('rate-limit');
		} else if (!latestRelease) {
			latestLabel.textContent = 'Could not check for updates.';
			latestLabel.classList.add('warning');
		} else if (isBleedingEdge) {
			latestLabel.textContent = `Switch back to ${latestRelease.name} from the pre-\u2060release`;
			latestLabel.classList.add('warning');
		} else if (latestRelease.name === updateData.version) {
			latestLabel.textContent = `Up to date (${latestRelease.name})`;
			latestLabel.classList.add('success');
		} else {
			latestLabel.textContent = `Update available: ${latestRelease.name}`;
			latestLabel.classList.add('warning');
		}
		// Already on the latest release: the label above says so and the
		// button is hidden rather than relabelled to describe that state.
		const latestAction = latestRelease && shouldSwitchToLatestRelease()
			? 'Switch to latest release'
			: latestRelease && latestRelease.name === updateData.version
				? ''
				: 'Update to latest release';
		const latestButton = byId('latest-release-btn');
		if (latestAction) latestButton.textContent = latestAction;
		setVisible(latestButton, Boolean(latestAction));

		if (!list.some((release) => release.name === selectedRelease)) {
			selectedRelease = list[0] ? list[0].name : '';
		}
		renderReleaseOptions();
	}

	function renderReleaseOptions() {
		const list = updateData && Array.isArray(updateData.releases)
			? updateData.releases
			: [];
		const query = byId('release-select').querySelector('.custom-select-search')
			.value.trim().toLocaleLowerCase();
		const normalizedQuery = normalizeTagSearch(query);
		const entries = list
			.filter((release) => normalizeTagSearch(release.name).includes(normalizedQuery))
			.map((release) => ({ value: release.name, label: release.name }));
		const selected = list.find((release) => release.name === selectedRelease);
		const releaseError = updateData && updateData.releases_error;
		const noReleasesLabel = releaseError === 'rate_limited'
			? '-'
			: releaseError ? 'Could not load releases' : 'No releases found';
		setDropdownLabel(
			'release-select',
			selected ? selected.name : list.length ? '' : noReleasesLabel,
		);
		const pagination = byId('release-pagination');
		const pageCount = Math.max(1, Math.ceil(entries.length / DROPDOWN_PAGE_SIZE));
		releasePage = Math.min(releasePage, pageCount - 1);
		const visibleEntries = entries.slice(
			releasePage * DROPDOWN_PAGE_SIZE,
			(releasePage + 1) * DROPDOWN_PAGE_SIZE,
		);
		pagination.classList.toggle('hidden', list.length <= DROPDOWN_PAGE_SIZE);
		const first = releasePage * DROPDOWN_PAGE_SIZE + 1;
		const last = releasePage * DROPDOWN_PAGE_SIZE + visibleEntries.length;
		setText(
			'release-page-summary',
			visibleEntries.length ? `Showing release ${first} to ${last}` : 'No matching releases',
		);
		byId('release-page-prev').disabled = releasePage === 0;
		byId('release-page-next').disabled = releasePage >= pageCount - 1;
		renderOptions(
			'release-select',
			visibleEntries,
			selectedRelease,
			list.length ? 'No matching releases' : noReleasesLabel,
			(entry) => {
				selectedRelease = entry.value;
				setDropdownLabel('release-select', entry.label);
				closeDropdown(byId('release-select'));
				updateButtonStates();
			},
		);
		syncDropdownScrollSpace();
	}

	function renderVersion(data) {
		const state = data.state || {};
		let version = '';
		if (state.source_type === 'branch' && state.source_name === 'main') {
			version = 'Pre-release';
		} else if (state.source_type === 'branch') {
			version = `Branch ${state.source_name}`;
		} else if (state.source_type === 'pr') {
			version = `PR #${state.source_name}`;
		} else if (state.source_type === 'tag') {
			version = `Tag ${state.source_name}`;
		} else if (state.source_type === 'release') {
			version = '';
		} else if (data.experimental_build) {
			version = 'Experimental build';
		}
		const installed = byId('installed-version');
		installed.replaceChildren(document.createTextNode(`Installed: ${data.version}`));
		if (version) {
			const source = document.createElement('span');
			source.className = 'installed-source-detail';
			source.textContent = version;
			installed.appendChild(source);
		}
	}

	function renderSourceTypes() {
		const types = [
			{ value: 'pr', label: 'Pull request' },
			{ value: 'branch', label: 'Side branch' },
			{ value: 'tag', label: 'Tag' },
		];
		const selected = types.find((entry) => entry.value === selectedSourceType) || types[0];
		setDropdownLabel('source-type-select', selected.label);
		renderOptions(
			'source-type-select',
			types,
			selectedSourceType,
			'No source types found',
			(entry) => {
				selectedSourceType = entry.value;
				selectedTarget = '';
				sourcePage = 0;
				clearTimeout(prLookupTimer);
				prLookupFailure = null;
				byId('pr-search').value = '';
				setDropdownLabel('source-type-select', entry.label);
				closeDropdown(byId('source-type-select'));
				renderDeveloperTargets(true);
			},
		);
	}

	function normalizeTagSearch(value) {
		return value.toLocaleLowerCase().replace(/[.-]/g, '');
	}

	// The pull request number the search names ("1055" or "#1055"), else ''.
	function searchedPullRequestNumber() {
		const match = byId('pr-search').value.trim().match(/^#?(\d{1,12})$/);
		const number = match ? Number(match[1]) : 0;
		return number >= 1 ? String(number) : '';
	}

	// The loaded pull requests and the ones found by number, one entry per
	// number. Found ones come first (newest lookup on top); a loaded entry
	// wins over a found one, since it was fetched more recently.
	function pullRequestEntries() {
		const loaded = developerData && Array.isArray(developerData.prs)
			? developerData.prs
			: [];
		const loadedNumbers = new Set(loaded.map((entry) => String(entry.number)));
		const found = [...lookedUpPrs.values()]
			.filter((entry) => !loadedNumbers.has(String(entry.number)))
			.reverse();
		return [...found, ...loaded];
	}

	function isListedPullRequest(number) {
		return pullRequestEntries().some((entry) => String(entry.number) === number);
	}

	// The number the current search should look up: a pull request number
	// that no listed entry has and GitHub has not already answered for.
	function pullRequestNumberToLookUp() {
		if (!developerData || selectedSourceType !== 'pr') return '';
		const number = searchedPullRequestNumber();
		if (!number || prLookupOutcomes.has(number) || isListedPullRequest(number)) return '';
		return number;
	}

	// Called on every keystroke in the search; the lookup starts once typing
	// pauses, and a number already on its way is not asked for twice.
	function schedulePullRequestLookup() {
		clearTimeout(prLookupTimer);
		if (!pullRequestNumberToLookUp()) return;
		prLookupTimer = setTimeout(() => {
			const number = pullRequestNumberToLookUp();
			if (!number || prLookupsInFlight.has(number)) return;
			prLookupsInFlight.add(number);
			renderDeveloperTargets(false);
			if (bridge) bridge.lookupPullRequest(number);
		}, PR_LOOKUP_DELAY_MS);
	}

	// The status line the search's lookup puts above the options, or null.
	function pullRequestLookupNotice() {
		const number = selectedSourceType === 'pr' ? searchedPullRequestNumber() : '';
		if (!number || isListedPullRequest(number)) return null;
		const outcome = prLookupOutcomes.get(number);
		if (outcome === 'merged') return { text: `#${number} was merged`, tone: 'neutral' };
		if (outcome === 'closed') return { text: `#${number} is closed`, tone: 'neutral' };
		if (outcome === 'not_found') return { text: `No pull request #${number}`, tone: 'neutral' };
		if (prLookupFailure && prLookupFailure.number === number) {
			if (prLookupFailure.status === 'rate_limited') {
				return { text: RATE_LIMIT_MESSAGE, tone: 'rate-limit' };
			}
			if (prLookupFailure.status === 'connection') {
				return {
					text: 'Could not reach GitHub. Check your connection and try again.',
					tone: 'error',
				};
			}
			return {
				text: `GitHub could not look up #${number}. Please try again later.`,
				tone: 'warning',
			};
		}
		if (prLookupsInFlight.has(number)) {
			return { text: `Looking up #${number}`, tone: 'checking' };
		}
		return null;
	}

	// A status line at the top of a dropdown's list, in place of its "no
	// matches" line. It is not an option, so the keyboard skips it.
	function showOptionsNotice(dropdown, notice) {
		const options = dropdown.querySelector('.custom-select-options');
		const empty = options.querySelector('.custom-select-empty');
		if (empty) empty.remove();
		const line = document.createElement('div');
		line.className = `custom-select-notice status-text ${notice.tone}`;
		line.textContent = notice.text;
		options.prepend(line);
	}

	function renderDeveloperTargets(resetSelection = false) {
		if (!developerData) return;
		const type = selectedSourceType;
		const fields = byId('target-fields');
		const search = byId('pr-search');
		const label = byId('target-label');
		const pagination = byId('pr-pagination');
		const targetMenu = byId('source-target').querySelector('.custom-select-menu');
		const options = {
			pr: { label: 'Pull request:', entries: pullRequestEntries() },
			branch: {
				label: 'Side branch:',
				entries: (developerData.branches || []).filter((entry) => entry.name !== 'main'),
			},
			tag: { label: 'Tag:', entries: developerData.tags || [] },
		};
		targetMenu.classList.remove('paged-menu');
		fields.classList.remove('hidden');
		label.textContent = options[type].label;
		search.placeholder = type === 'pr'
			? 'Search pull requests by number or title'
			: `Search ${type === 'branch' ? 'side branches' : 'tags'}`;
		search.setAttribute('aria-label', type === 'pr'
			? 'Search pull requests'
			: `Search ${type === 'branch' ? 'side branches' : 'tags'}`);
		const query = search.value.trim().toLocaleLowerCase();
		const numberQuery = query.startsWith('#') ? query.slice(1) : query;
		const entries = options[type].entries.filter((entry) => {
			if (!query) return true;
			if (type === 'pr') {
				return String(entry.number).toLocaleLowerCase().includes(numberQuery) ||
					String(entry.title || '').toLocaleLowerCase().includes(query);
			}
			if (type === 'tag') {
				return normalizeTagSearch(entry.name).includes(normalizeTagSearch(query));
			}
			return String(entry.name).toLocaleLowerCase().includes(query);
		});
		const pageCount = Math.max(1, Math.ceil(entries.length / DROPDOWN_PAGE_SIZE));
		sourcePage = Math.min(sourcePage, pageCount - 1);
		const visibleEntries = type === 'pr' || type === 'tag'
			? entries.slice(sourcePage * DROPDOWN_PAGE_SIZE, (sourcePage + 1) * DROPDOWN_PAGE_SIZE)
			: entries;
		const showPagination = type === 'pr' ||
			(type === 'tag' && options[type].entries.length > DROPDOWN_PAGE_SIZE);
		pagination.classList.toggle('hidden', !showPagination);
		pagination.setAttribute(
			'aria-label',
			type === 'tag' ? 'Tag pages' : 'Pull request pages',
		);
		targetMenu.classList.toggle('paged-menu', showPagination);
		// The footer is the two page arrows only; their disabled states are
		// the page indicator.
		if (showPagination) {
			byId('pr-page-prev').setAttribute(
				'aria-label',
				type === 'tag' ? 'Previous tag page' : 'Previous pull request page',
			);
			byId('pr-page-next').setAttribute(
				'aria-label',
				type === 'tag' ? 'Next tag page' : 'Next pull request page',
			);
			byId('pr-page-prev').disabled = sourcePage === 0;
			byId('pr-page-next').disabled = sourcePage >= pageCount - 1;
		}
		const valueFor = (entry) => type === 'pr' ? String(entry.number) : entry.name;
		const labelFor = (entry) => type === 'pr'
			? `#${entry.number} — ${entry.title}`
			: entry.name;
		// A pull request found by number is listed first but never becomes
		// the default pick; the first loaded one stays that.
		const defaultEntry = (type === 'pr' && (developerData.prs || [])[0]) ||
			options[type].entries[0];
		if (resetSelection || !options[type].entries.some((entry) => valueFor(entry) === selectedTarget)) {
			selectedTarget = type === 'branch' &&
				options[type].entries.some((entry) => entry.name === 'main')
				? 'main'
				: (defaultEntry ? valueFor(defaultEntry) : '');
		}
		const selectedEntry = options[type].entries.find((entry) => valueFor(entry) === selectedTarget);
		const error = developerData.errors && developerData.errors[type];
		const emptyLabels = {
			pr: 'No PRs found',
			branch: 'No branches found',
			tag: 'No tags found',
		};
		const unavailableLabels = {
			pr: 'Could not load PRs',
			branch: 'Could not load branches',
			tag: 'Could not load tags',
		};
		const noSources = options[type].entries.length === 0;
		const rateLimited = noSources && error === 'rate_limited';
		const emptyLabel = rateLimited
			? '-'
			: error ? unavailableLabels[type] : emptyLabels[type];
		const targetDropdown = byId('source-target');
		const targetTrigger = targetDropdown.querySelector('.custom-select-trigger');
		targetTrigger.disabled = noSources;
		if (noSources) closeDropdown(targetDropdown);
		setText('source-rate-limit-status', rateLimited ? RATE_LIMIT_MESSAGE : '');
		setVisible(byId('source-rate-limit-status'), rateLimited);
		setDropdownLabel(
			'source-target',
			selectedEntry ? labelFor(selectedEntry) : noSources ? emptyLabel : '',
		);
		renderOptions(
			'source-target',
			visibleEntries.map((entry) => ({ value: valueFor(entry), label: labelFor(entry) })),
			selectedTarget,
			noSources
				? emptyLabel
				: type === 'pr' ? 'No matching open pull requests' :
					(type === 'branch' ? 'No matching side branches' : 'No matching tags'),
			(entry) => {
				selectedTarget = entry.value;
				setDropdownLabel('source-target', entry.label);
				closeDropdown(byId('source-target'));
				updateButtonStates();
			},
		);
		// A search for a pull request number the list lacks: "Looking up",
		// then why it cannot be listed. An open one is listed as an option.
		let lookupNotice = type === 'pr' ? pullRequestLookupNotice() : null;
		// "No pull request #10" would contradict loaded matches such as #1055.
		if (lookupNotice && lookupNotice.tone === 'neutral' &&
			!targetDropdown.querySelector('.custom-select-empty')) lookupNotice = null;
		if (lookupNotice) showOptionsNotice(targetDropdown, lookupNotice);
		// Screen readers hear the same line, or the option a lookup found;
		// the list itself is rebuilt on every keystroke and is not a live region.
		const searchedNumber = type === 'pr' ? searchedPullRequestNumber() : '';
		const foundEntry = searchedNumber ? lookedUpPrs.get(searchedNumber) : null;
		const lookupMessage = lookupNotice
			? lookupNotice.text
			: foundEntry ? labelFor(foundEntry) : '';
		const lookupStatus = byId('pr-lookup-status');
		if (lookupStatus.textContent !== lookupMessage) lookupStatus.textContent = lookupMessage;
		if (type === 'pr' || type === 'tag') {
			byId('source-target').querySelector('.custom-select-options').scrollTop = 0;
		}
		updateButtonStates();
		syncDropdownScrollSpace();
	}

	function switchTab(tab) {
		document.querySelectorAll('.update-tab-link').forEach((button) => {
			markSidebarTab(button, button.dataset.tab === tab);
		});
		document.querySelectorAll('.tab-panel').forEach((panel) => {
			panel.classList.toggle('active', panel.id === `panel-${tab}`);
		});
		byId('github-link').classList.toggle('hidden', tab === 'sprites');
		if (tab === 'developer' && !developerData && !developerLoading && bridge) {
			developerLoading = true;
			developerLoadFailed = false;
			setDeveloperStatus('Loading developer options');
			bridge.loadDeveloperData();
		}
		if (tab === 'developer') {
			switchDeveloperView(selectedDeveloperView);
			showDeveloperWarningIfNeeded();
		} else if (tab === 'releases') {
			switchReleaseView(selectedReleaseView);
		}
	}

	function startInstall(sourceType, sourceName, progressKey) {
		activeProgressKey = progressKey;
		setActiveProgressMessage('Preparing update');
		setOperationProgress(0, true, true);
		setOperationStatus('', false);
		bridge.installUpdate(sourceType, sourceName, (result) => {
			if (result && result.success === false) {
				setOperationProgress(0, true);
				setActiveProgressMessage(
					result.message || 'Update could not be started.',
					'failure',
				);
			}
		});
	}

	function installedExternalSource() {
		if (!updateData) return null;
		const source = sourceLinkTarget('installed');
		return source.sourceType === 'pr' ||
			(source.sourceType === 'branch' && source.sourceName !== 'main')
			? source
			: null;
	}

	function installActionVerb(buttonLabel) {
		if (/^switch\b/i.test(buttonLabel)) return 'switch';
		if (/^(update|fast-forward)\b/i.test(buttonLabel)) return 'update';
		return 'install';
	}

	function install(
		sourceType,
		sourceName,
		_label,
		warning = '',
		progressKey = 'developer-source',
		replacementVerb = 'install',
	) {
		const prompt = updateData && updateData.git_clone
			? `Switch this Git checkout to `
			: `Update Ankimon to `;
		const resolvedTarget = sourceType === 'installed' && updateData
			? { type: updateData.source_type, name: updateData.source_name }
			: sourceType === 'current' && updateData
				? { type: 'branch', name: updateData.git_info && updateData.git_info.branch }
				: { type: sourceType, name: sourceName };
		if (!warning && resolvedTarget.type === 'pr') {
			warning = 'Anyone can open a pull request; this code is unreviewed and unreleased. Installing it runs unverified code on your device. It may or may not behave as expected, and can even break the add-on. Please proceed if you trust this PR. Use at your own risk.';
		} else if (!warning && resolvedTarget.type === 'branch' &&
			resolvedTarget.name && resolvedTarget.name !== 'main') {
			warning = 'These are collaborator branches, separate from the pre-release. The branch may be a work in progress, and may or may not behave as expected. Please proceed if you trust this branch. Use at your own risk.';
		}
		const isPullRequestOrSideBranch = resolvedTarget.type === 'pr' ||
			(resolvedTarget.type === 'branch' && resolvedTarget.name !== 'main');
		const developerSourceInstall = progressKey === 'developer-source' &&
			isPullRequestOrSideBranch;
		const linkTarget = sourceLinkTarget(sourceType, sourceName);
		const replacingSource = sourceType !== 'installed' &&
			sourceType !== 'current' &&
			progressKey !== 'developer-current'
			? installedExternalSource()
			: null;
		const fragment = document.createDocumentFragment();
		if (warning) {
			const warningElement = document.createElement('p');
			warningElement.className = 'warning-text modal-warning-text confirm-update-warning';
			const heading = document.createElement('strong');
			heading.textContent = 'Warning: ';
			const riskText = 'Use at your own risk.';
			if (isPullRequestOrSideBranch && warning.endsWith(riskText)) {
				const risk = document.createElement('strong');
				risk.textContent = riskText;
				warningElement.append(
					heading,
					document.createTextNode(warning.slice(0, -riskText.length)),
					risk,
				);
			} else {
				warningElement.append(heading, document.createTextNode(warning));
			}
			fragment.append(warningElement, document.createTextNode('\n'));
		}
		if (developerSourceInstall) {
			const userFilesWarning = document.createElement('p');
			userFilesWarning.className =
				'warning-text modal-warning-text user-files-warning';
			const userFilesLead = document.createElement('strong');
			userFilesLead.textContent = 'Please check if the source modifies user_files/';
			userFilesWarning.append(
				userFilesLead,
				document.createTextNode(
					', which stores your Ankimon data. If it does, you may want to back up your files in Ankimon > Game > Backup Manager before proceeding.',
				),
			);
			fragment.append(userFilesWarning, document.createTextNode('\n'));
		}
		const createSourceLink = (target, label) => {
			const source = document.createElement('a');
			source.href = '#';
			source.className = 'source-link';
			source.textContent = label;
			source.addEventListener('click', (event) => {
				event.preventDefault();
				if (bridge) {
					bridge.openGitHubTarget(
						sourceUrl(target.sourceType, target.sourceName),
						(opened) => {
							if (!opened) {
								showToast('Could not open the selected source in your browser.', true);
							}
						},
					);
				}
			});
			return source;
		};
		if (replacingSource) {
			const installedLabel = replacingSource.sourceType === 'pr'
				? sourceLinkLabel(replacingSource.sourceType, replacingSource.sourceName)
				: `Branch ${replacingSource.sourceName}`;
			fragment.append(
				document.createTextNode('You currently have '),
				createSourceLink(replacingSource, installedLabel),
				document.createTextNode(
					` installed, which will be removed once you ${replacementVerb}.\n\n`,
				),
			);
		}
		fragment.appendChild(document.createTextNode(prompt));
		// Users know the main branch as "the pre-release"; the link still opens
		// the real branch. A Git checkout keeps the actual branch name.
		const preReleaseTarget = !(updateData && updateData.git_clone) &&
			linkTarget.sourceType === 'branch' &&
			(linkTarget.sourceName || 'main') === 'main';
		if (preReleaseTarget) fragment.appendChild(document.createTextNode('the '));
		const link = createSourceLink(
			linkTarget,
			preReleaseTarget
				? 'pre-release'
				: sourceLinkLabel(linkTarget.sourceType, linkTarget.sourceName),
		);
		fragment.appendChild(link);
		if (updateData && updateData.git_clone) {
			fragment.appendChild(document.createTextNode('?\n\nYour local branches and commits will not be reset. The checkout must be clean, and Anki must be restarted afterward.'));
		} else if (isPullRequestOrSideBranch) {
			fragment.appendChild(document.createTextNode('?'));
		} else {
			fragment.appendChild(
				document.createTextNode(
					'?\n\nYour Ankimon progress and configuration will not be lost.',
				),
			);
		}
		const proceed = () => startInstall(sourceType, sourceName, progressKey);
		// Title and confirm button repeat the verb of the button that opened this.
		const verb = ['update', 'switch'].includes(replacementVerb) ? replacementVerb : 'install';
		showModal(
			`Confirm ${verb}`,
			fragment,
			verb.charAt(0).toUpperCase() + verb.slice(1),
			isHighRiskTarget(resolvedTarget)
				? () => showHighRiskConfirmation(resolvedTarget, linkTarget, replacementVerb, proceed)
				: proceed,
			true,
			Boolean(warning),
		);
	}

	// The Developer tag list also contains the tags of published releases.
	function isPublishedReleaseTag(name) {
		return Boolean(
			updateData && Array.isArray(updateData.releases) &&
			updateData.releases.some((release) => release.name === name),
		);
	}

	// Only a stable release has been through the release process. main, side
	// branches, pull requests and unpublished tags all get the second
	// confirmation.
	function isHighRiskTarget(target) {
		if (!target) return true;
		if (target.type === 'release') return false;
		return !(target.type === 'tag' && isPublishedReleaseTag(target.name));
	}

	function showHighRiskConfirmation(target, linkTarget, verb, onConfirm) {
		const label = sourceLinkLabel(linkTarget.sourceType, linkTarget.sourceName);
		const isMain = target.type === 'branch' && (target.name || 'main') === 'main';
		const unreleased = ' It has not been through a stable release and can break the add-on.';
		const unlisted = ' It may be untested and can break the add-on.';
		const description = target.type === 'pr'
			? `${label} is an unreviewed pull request.${unreleased}`
			: isMain
				? `The pre-release is the newest unreleased code.${unreleased}`
				: target.type === 'branch'
					? `Branch ${label} is an unreleased side branch.${unreleased}`
					: target.type === 'tag'
						? `Tag ${label} is not in the list of published releases.${unlisted}`
						: `${label} is not a published release.${unlisted}`;
		const content = document.createElement('div');
		const banner = document.createElement('p');
		banner.className = 'warning-text danger-text modal-warning-text';
		const lead = document.createElement('strong');
		lead.textContent = 'High risk: ';
		banner.append(lead, document.createTextNode(description));
		const advice = document.createElement('p');
		advice.className = 'high-risk-advice';
		advice.textContent = 'This cannot be undone automatically. If you are unsure, cancel and create a backup first in Ankimon > Game > Backup Manager.';
		content.append(banner, advice);
		const confirmLabel = verb === 'switch'
			? 'Switch anyway'
			: verb === 'update' ? 'Update anyway' : 'Install anyway';
		showModal(
			'High-risk update',
			content,
			confirmLabel,
			onConfirm,
			true,
			'danger',
			true,
			HIGH_RISK_ARM_DELAY_MS,
		);
	}

	function showReleaseSwitchConfirmation() {
		if (!latestRelease) return;
		const content = document.createElement('div');
		const warning = document.createElement('p');
		warning.className = 'warning-text modal-warning-text';
		const heading = document.createElement('strong');
		heading.textContent = 'Warning: ';
		warning.append(
			heading,
			document.createTextNode("Some of the comfort features that you've been using might vanish, as this release might be far behind."),
		);
		const list = document.createElement('ul');
		list.className = 'commit-list lost-commit-list';
		const commits = lostReleaseCommits.slice(0, 100);
		commits.forEach((commit) => {
			const item = document.createElement('li');
			const sha = String(commit.sha || '');
			if (/^[\da-f]{7,40}$/i.test(sha)) {
				const link = document.createElement('a');
				link.href = '#';
				link.className = 'commit-link';
				link.textContent = sha;
				link.addEventListener('click', (event) => {
					event.preventDefault();
					openGitHubTarget(
						`https://github.com/h0tp-ftw/ankimon/commit/${encodeURIComponent(sha)}`,
						'Could not open the commit in your browser.',
					);
				});
				item.append(link, document.createTextNode(commit.message || ''));
			} else {
				const code = document.createElement('code');
				code.textContent = sha;
				item.append(code, document.createTextNode(commit.message || ''));
			}
			list.appendChild(item);
		});
		if (lostReleaseCommits.length > 100) {
			const more = document.createElement('li');
			more.className = 'lost-commits-more';
			more.textContent = '...And more.';
			list.appendChild(more);
		}
		if (!commits.length) {
			const empty = document.createElement('li');
			empty.textContent = 'No intervening commits were found.';
			list.appendChild(empty);
		}
		content.append(warning, list);
		const releaseName = latestRelease.name;
		showModal(
			'Updates that will be lost',
			content,
			'Switch',
			() => startInstall('release', releaseName, 'release'),
			true,
			true,
		);
	}

	function openGitHubTarget(target, errorMessage) {
		if (!bridge) return;
		bridge.openGitHubTarget(target, (opened) => {
			if (!opened) showToast(errorMessage, true);
		});
	}

	function sourceLinkTarget(sourceType, sourceName) {
		if (sourceType === 'installed' && updateData) {
			return {
				sourceType: updateData.source_type || 'branch',
				sourceName: updateData.source_name || 'main',
			};
		}
		if (sourceType === 'current' && updateData && updateData.git_info) {
			return {
				sourceType: 'branch',
				sourceName: updateData.git_info.branch || 'main',
			};
		}
		return { sourceType, sourceName };
	}

	function sourceLinkLabel(sourceType, sourceName) {
		if (sourceType === 'pr') return `PR #${sourceName}`;
		return sourceName || 'main';
	}

	function sourceUrl(sourceType, sourceName) {
		const target = sourceLinkTarget(sourceType, sourceName);
		const name = target.sourceName;
		const ref = (name || 'main').split('/').map(encodeURIComponent).join('/');
		if (target.sourceType === 'pr') return `https://github.com/h0tp-ftw/ankimon/pull/${ref}`;
		if (target.sourceType === 'release') return `https://github.com/h0tp-ftw/ankimon/releases/tag/${ref}`;
		if (target.sourceType === 'tag') return `https://github.com/h0tp-ftw/ankimon/tree/${ref}`;
		return `https://github.com/h0tp-ftw/ankimon/tree/${ref}`;
	}

	function installActiveBranch() {
		if (!updateData) return;
		const hostId = byId('branch-update-view').parentElement.id;
		const progressKey = hostId === 'release-view-pre-release'
			? 'pre-release'
			: hostId === 'developer-view-main'
				? 'developer-main'
				: 'developer-current';
		if (selectedBranchView === 'main') {
			install(
				'branch',
				'main',
				'latest main',
				'',
				progressKey,
				installActionVerb(byId('branch-update-btn').textContent),
			);
			return;
		}
		if (updateData.show_branch_views) {
			install(
				'installed',
				'current',
				`latest installed source (${updateData.active_branch})`,
				'',
				progressKey,
				installActionVerb(byId('branch-update-btn').textContent),
			);
			return;
		}
		const name = updateData.active_branch || 'main';
		if (updateData.git_clone) {
			install(
				'current',
				'current',
				name,
				'',
				progressKey,
				installActionVerb(byId('branch-update-btn').textContent),
			);
		} else {
			install(
				'branch',
				updateData.source_name || 'main',
				`latest ${name}`,
				'',
				progressKey,
				installActionVerb(byId('branch-update-btn').textContent),
			);
		}
	}

	window.onUpdateData = (data) => {
		updateData = data;
		spriteCheckUpToDate = Boolean(data.sprite_check_up_to_date);
		renderVersion(data);
		renderBranch(data);
		renderReleases(data.releases, data.releases_error);
		const activeTab = document.querySelector('.update-tab-link.active');
		if (activeTab && activeTab.dataset.tab === 'releases' &&
			selectedReleaseView === 'pre-release') {
			// The branch view is shared; a reload must not move it (or retitle it)
			// out from under the Pre-release tab the user is looking at.
			switchReleaseView('pre-release');
		} else {
			switchDeveloperView(selectedDeveloperView);
		}
		byId('branch-snooze').checked = Number(data.skip_until) > Date.now() / 1000;
		byId('sprites-snooze').checked = Boolean(data.sprite_snoozed);
		updateButtonStates();
		if (developerData) renderDeveloperTargets();
	};

	window.onDeveloperData = (data) => {
		developerData = data;
		developerLoading = false;
		developerLoadFailed = false;
		setDeveloperStatus('');
		renderDeveloperTargets();
	};

	// The reply to bridge.lookupPullRequest. An answer is kept for the session
	// even when the search has moved on (an open pull request is installable
	// from now on), but only a reply for the current search is shown.
	window.onPullRequestLookup = (data) => {
		const number = String((data && data.number) || '');
		const status = data && data.status;
		prLookupsInFlight.delete(number);
		if (status === 'open' && data.pr) {
			lookedUpPrs.delete(number);
			lookedUpPrs.set(number, data.pr);
		} else if (['closed', 'merged', 'not_found'].includes(status)) {
			prLookupOutcomes.set(number, status);
		}
		if (selectedSourceType !== 'pr' || searchedPullRequestNumber() !== number) return;
		if (!['open', 'closed', 'merged', 'not_found'].includes(status)) {
			prLookupFailure = { number, status };
		}
		// Enter should pick the pull request that was typed, not a partial
		// match that held the highlight while the lookup ran.
		if (status === 'open' && data.pr) byId('source-target').dataset.activeValue = number;
		renderDeveloperTargets(false);
	};

	window.onDeveloperLoadFailed = (data) => {
		developerLoading = false;
		developerLoadFailed = true;
		setDeveloperStatus(`Could not load developer options: ${data.message}`);
	};

	window.onUpdateLoadFailed = (data) => {
		setOperationStatus(`Could not check for updates: ${data.message}`);
		// Nothing else will resolve a line still showing the spinner.
		['latest-release-label', 'branch-status'].forEach((id) => {
			const line = byId(id);
			if (!line.classList.contains('checking')) return;
			line.classList.remove(...STATUS_TONES);
			line.classList.add('error');
			line.textContent = 'Could not check for updates';
		});
	};

	window.onUpdateCodeError = (data) => {
		setCodeError(data.message, data.traceback);
	};

	window.onUpdateOperation = (data) => {
		if (data.busy) busyOperations.add(data.kind);
		else busyOperations.delete(data.kind);
		if (data.busy && data.kind === 'load') setOperationStatus('Checking for updates');
		if (data.busy && data.kind === 'developer') {
			developerLoading = true;
			developerLoadFailed = false;
			setDeveloperStatus('Loading developer options');
		}
		if (!data.busy && data.kind === 'developer' && !developerData && !developerLoadFailed) {
			developerLoading = false;
			setDeveloperStatus('');
		}
		if (data.busy && data.kind === 'sprites-check') {
			setSpriteResult('');
			setSpriteProgress(0, 'Checking for sprite updates');
			byId('sprites-progress').classList.remove('hidden');
		}
		if (data.busy && data.kind === 'sprites-install') {
			setSpriteResult('');
			byId('sprites-progress').classList.remove('hidden');
			setSpriteProgress(0, 'Updating sprites');
		}
		if (!data.busy && data.kind === 'load' && !currentBusy()) setOperationStatus('', false);
		updateButtonStates();
	};

	window.onUpdateInstallStarted = (data) => {
		setOperationStatus('', false);
		setActiveProgressMessage(
			data.label ? `Downloading ${data.label}` : 'Preparing update',
		);
		setOperationProgress(0, true, !updateData || !updateData.git_clone);
	};

	window.onUpdateProgress = (data) => setOperationProgress(data.percent, true);
	window.onUpdateStatus = (data) => {
		setOperationStatus('', false);
		setActiveProgressMessage(data.message);
	};
	window.onSpritesCheckProgress = (data) => {
		setSpriteProgress(data.percent, data.message || 'Checking for sprite updates');
	};

	window.onUpdateFinished = (data) => {
		const progressMessage = data.success
			? `Update complete. ${(data.status || data.message || '')
				.replace(/^Update complete[.!]?\s*/i, '')}`.trim()
			: (data.message || 'Update failed.');
		setActiveProgressMessage(
			progressMessage,
			data.success ? 'complete' : 'failure',
		);
		setOperationStatus('', false);
		setOperationProgress(data.success ? 100 : 0, true);
		if (data.success) {
			if (bridge) bridge.loadData();
			showModal(
				'Update complete',
				'Update applied successfully. Please restart Anki for changes to take effect.',
				'Close',
				null,
				false,
			);
		} else {
			showModal('Update failed', data.message || 'The update failed.', 'Close', null, false);
		}
	};

	window.onSpritesChecked = (result) => {
		byId('sprites-install-btn').classList.add('hidden');
		spriteUpdateAvailable = result.status === 'update_available';
		spriteCheckUpToDate = result.status === 'up_to_date';
		const count = (paths) => (Array.isArray(paths) ? paths.length : 0);
		if (result.status === 'up_to_date') {
			setSpriteResult('Sprites are up to date', 'success');
		} else if (result.status === 'error') {
			setSpriteResult(
				result.rate_limited
					? RATE_LIMIT_MESSAGE
					: `Error checking updates: ${result.error}`,
				result.rate_limited ? 'rate-limit' : 'error',
			);
		} else if (spriteUpdateAvailable) {
			setSpriteResult('Sprite update available', 'warning', {
				added: count(result.added),
				modified: count(result.modified),
				deleted: count(result.deleted),
			});
			byId('sprites-install-btn').classList.remove('hidden');
		}
		updateButtonStates();
	};

	window.onSpritesCheckFailed = (data) => {
		const rateLimited = data.rate_limited;
		setSpriteResult(
			rateLimited
				? RATE_LIMIT_MESSAGE
				: `Error checking sprite updates: ${data.message}`,
			rateLimited ? 'rate-limit' : 'error',
		);
		spriteCheckUpToDate = false;
	};

	window.onSpriteProgress = (data) => {
		setSpriteProgress(data.percent);
	};

	window.onSpriteStatus = (data) => {
		setSpriteProgress(
			Number(byId('sprites-progress').querySelector('progress').value),
			data.message,
		);
	};

	window.onSpritesFinished = (data) => {
		spriteUpdateAvailable = false;
		byId('sprites-install-btn').classList.add('hidden');
		const message = data.success
			? `Update complete. ${data.message}`
			: `Update failed: ${data.message}`;
		setSpriteProgress(data.success ? 100 : Number(
			byId('sprites-progress').querySelector('progress').value,
		), message, data.success ? 'complete' : 'failure');
	};

	window.refreshUpdateManager = () => {
		if (bridge) bridge.loadData();
	};

	document.querySelectorAll('.update-tab-link').forEach((button) => {
		button.addEventListener('click', () => switchTab(button.dataset.tab));
	});
	byId('developer-warning-info').addEventListener('click', () => {
		showDeveloperWarningIfNeeded(true);
	});
	byId('developer-subtabs').querySelectorAll('[data-developer-view]').forEach((button) => {
		button.addEventListener('click', () => switchDeveloperView(button.dataset.developerView));
	});
	byId('release-subtabs').querySelectorAll('[data-release-view]').forEach((button) => {
		button.addEventListener('click', () => switchReleaseView(button.dataset.releaseView));
	});
	wireTabKeys(document.querySelector('.sidebar-nav'), '.update-tab-link', 'ArrowUp', 'ArrowDown');
	wireTabKeys(byId('release-subtabs'), '.branch-subtab', 'ArrowLeft', 'ArrowRight');
	wireTabKeys(byId('developer-subtabs'), '.branch-subtab', 'ArrowLeft', 'ArrowRight');
	// Initial state, before any switch function has run.
	document.querySelectorAll('.update-tab-link').forEach((button) => {
		markSidebarTab(button, button.classList.contains('active'));
	});
	document.querySelectorAll('.branch-subtab').forEach((button) => {
		markSubtab(button, button.classList.contains('active'));
	});

	document.querySelectorAll('.custom-select').forEach((dropdown) => wireDropdown(dropdown));
	document.addEventListener('click', () => closeDropdowns());
	document.addEventListener('keydown', (event) => {
		if (event.key === 'Escape') closeDropdowns();
	});
	renderSourceTypes();
	// Typing starts a new search, so its first match is highlighted rather
	// than the option kept from before; paging and reloads still keep it.
	byId('release-select').querySelector('.custom-select-search')
		.addEventListener('input', () => {
			releasePage = 0;
			byId('release-select').dataset.activeValue = '';
			renderReleaseOptions();
		});
	byId('pr-search').addEventListener('input', () => {
		sourcePage = 0;
		byId('source-target').dataset.activeValue = '';
		// A new search retries a lookup that failed (rate limit, offline).
		prLookupFailure = null;
		renderDeveloperTargets(false);
		schedulePullRequestLookup();
	});
	byId('pr-page-prev').addEventListener('click', () => {
		if (sourcePage > 0) {
			sourcePage -= 1;
			renderDeveloperTargets(false);
		}
	});
	byId('pr-page-next').addEventListener('click', () => {
		if (!byId('pr-page-next').disabled) {
			sourcePage += 1;
			renderDeveloperTargets(false);
		}
	});
	byId('release-page-prev').addEventListener('click', () => {
		if (releasePage > 0) {
			releasePage -= 1;
			renderReleaseOptions();
		}
	});
	byId('release-page-next').addEventListener('click', () => {
		if (!byId('release-page-next').disabled) {
			releasePage += 1;
			renderReleaseOptions();
		}
	});
	window.addEventListener('resize', syncDropdownScrollSpace);
	byId('github-link').addEventListener('click', () => {
		if (bridge) {
			bridge.openGitHub((opened) => {
				if (!opened) showToast('Could not open Ankimon on GitHub.', true);
			});
		}
	});

	byId('branch-snooze').addEventListener('change', (event) => {
		if (bridge) {
			bridge.setBranchSnooze(event.target.checked, (saved) => {
				if (!saved) showToast('Could not save update snooze setting.', true);
			});
		}
	});

	byId('sprites-snooze').addEventListener('change', (event) => {
		if (bridge) {
			bridge.setSpriteSnooze(event.target.checked, (saved) => {
				if (!saved) showToast('Could not save sprite snooze setting.', true);
			});
		}
	});

	byId('branch-update-btn').addEventListener('click', installActiveBranch);
	byId('git-pull-btn').addEventListener('click', () => {
		const branch = updateData && updateData.git_info && updateData.git_info.branch;
		install(
			'current',
			'current',
			branch || 'current Git branch',
			'',
			'git',
			installActionVerb(byId('git-pull-btn').textContent),
		);
	});
	byId('latest-release-btn').addEventListener('click', () => {
		if (latestRelease) {
			if (isBleedingEdge) {
				showReleaseSwitchConfirmation();
			} else {
				install(
					'release',
					latestRelease.name,
					`latest release (${latestRelease.name})`,
					'',
					'release',
					installActionVerb(byId('latest-release-btn').textContent),
				);
			}
		}
	});
	byId('release-install-btn').addEventListener('click', () => {
		const release = updateData && (updateData.releases || []).find(
			(item) => item.name === selectedRelease,
		);
		if (release) {
			install(
				'release',
				release.name,
				`release ${release.name}`,
				'',
				'release',
				installActionVerb(byId('release-install-btn').textContent),
			);
		}
	});

	byId('developer-install-btn').addEventListener('click', () => {
		const source = selectedSourceType;
		const value = selectedTarget;
		if (!value || !developerData) return;
		const entry = source === 'pr'
			? pullRequestEntries().find((pr) => String(pr.number) === value)
			: source === 'branch'
				? (developerData.branches || []).find((branch) => branch.name === value)
				: (developerData.tags || []).find((tag) => tag.name === value);
		if (!entry) return;
		install(
			source,
			value,
			source === 'pr' ? `PR #${entry.number} (${entry.title})` : `${source} ${value}`,
			'',
			'developer-source',
			installActionVerb(byId('developer-install-btn').textContent),
		);
	});

	byId('sprites-check-btn').addEventListener('click', () => {
		if (!bridge || spriteCheckUpToDate) return;
		spriteUpdateAvailable = false;
		byId('sprites-install-btn').classList.add('hidden');
		setSpriteResult('');
		setSpriteProgress(0, 'Checking for sprite updates');
		byId('sprites-progress').classList.remove('hidden');
		bridge.checkSprites();
	});

	byId('sprites-install-btn').addEventListener('click', () => {
		if (!bridge) return;
		bridge.installSprites((result) => {
			if (result && result.success === false) showToast(result.message, true);
		});
	});

	if (typeof qt !== 'undefined' && qt.webChannelTransport) {
		new QWebChannel(qt.webChannelTransport, (channel) => {
			bridge = channel.objects.update;
			navBridge = channel.objects.nav;
			window.wireNavSwitcher(navBridge);
			bridge.loadData();
		});
	}
})();
