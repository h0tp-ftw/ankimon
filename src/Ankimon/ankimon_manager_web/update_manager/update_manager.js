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
	let prPage = 0;
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
	let activeProgressPanel = '';
	let activeProgressBranchView = '';
	let activeProgressVisible = false;
	const busyOperations = new Set();
	let toastTimer = null;
	const RATE_LIMIT_MESSAGE = 'Connection rate limited, pika try again soon!';
	const MAIN_RATE_LIMIT_MESSAGE = 'Connection rate limited! Pika try again soon.';
	const SOURCE_RATE_LIMIT_MESSAGE = 'Connection rate limited, Pika try again!';
	const PR_PAGE_SIZE = 50;

	const byId = (id) => document.getElementById(id);
	const setText = (id, value) => { byId(id).textContent = value; };
	const setVisible = (element, visible) => element.classList.toggle('hidden', !visible);

	function showToast(message, isError = false) {
		const toast = byId('toast');
		toast.textContent = message;
		toast.classList.toggle('error', isError);
		toast.classList.add('visible');
		clearTimeout(toastTimer);
		toastTimer = setTimeout(() => toast.classList.remove('visible'), 3000);
	}

	function showModal(title, message, confirmLabel, onConfirm, showCancel = true, warningStyle = false, escapeCloses = true) {
		setText('modal-title', title);
		const messageElement = byId('modal-message');
		messageElement.replaceChildren();
		if (message instanceof Node) messageElement.appendChild(message);
		else messageElement.textContent = message;
		setText('modal-confirm', confirmLabel);
		setVisible(byId('modal-cancel'), showCancel);
		const modal = byId('update-modal');
		modal.classList.remove('hidden');
		const modalCard = modal.querySelector('.update-modal-card');
		modalCard.classList.toggle('warning-modal', warningStyle);
		const confirm = byId('modal-confirm');
		const cancel = byId('modal-cancel');
		const previous = document.activeElement;
		const onKey = (event) => {
			if (event.key === 'Escape' && escapeCloses) close();
		};
		const close = () => {
			modal.classList.add('hidden');
			modalCard.classList.remove('warning-modal');
			confirm.onclick = null;
			cancel.onclick = null;
			document.removeEventListener('keydown', onKey);
			if (previous && previous.focus) previous.focus();
		};
		confirm.onclick = () => {
			close();
			if (onConfirm) onConfirm();
		};
		cancel.onclick = close;
		document.addEventListener('keydown', onKey);
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
			const match = message.match(/^(Update complete!)(.*)$/);
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

	function setOperationProgress(value, visible = true, indeterminate = false) {
		if (!activeProgressPanel) return;
		const container = byId(`${activeProgressPanel}-progress`);
		const progress = container.querySelector('progress');
		if (indeterminate) {
			progress.removeAttribute('value');
			container.querySelector('output').textContent = '';
		} else {
			const percent = Math.max(0, Math.min(100, Number(value) || 0));
			progress.value = percent;
			container.querySelector('output').textContent = `${percent}%`;
		}
		activeProgressVisible = visible;
		if (activeProgressPanel === 'branch') {
			syncBranchProgressVisibility();
		} else {
			setVisible(container, visible);
		}
	}

	function syncBranchProgressVisibility() {
		const matchesView = !updateData || !updateData.show_branch_views ||
			selectedBranchView === activeProgressBranchView;
		setVisible(byId('branch-progress'), activeProgressVisible && matchesView);
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

	function setDeveloperStatus(message) {
		setText('developer-status', message || '');
		setVisible(byId('developer-status'), Boolean(message));
	}

	function closeDropdown(dropdown) {
		dropdown.classList.remove('open');
		dropdown.querySelector('.custom-select-menu').classList.add('hidden');
		dropdown.querySelector('.custom-select-trigger').setAttribute('aria-expanded', 'false');
		syncDropdownScrollSpace();
	}

	function syncDropdownScrollSpace() {
		const hasOpenDropdown = document.querySelector('.custom-select.open');
		document.querySelector('.update-content')
			.classList.toggle('has-open-dropdown', Boolean(hasOpenDropdown));
	}

	function closeDropdowns(except = null) {
		document.querySelectorAll('.custom-select.open').forEach((dropdown) => {
			if (dropdown !== except) closeDropdown(dropdown);
		});
	}

	function wireDropdown(dropdown, onOpen = null) {
		const trigger = dropdown.querySelector('.custom-select-trigger');
		const menu = dropdown.querySelector('.custom-select-menu');
		trigger.addEventListener('click', (event) => {
			event.stopPropagation();
			if (dropdown.classList.contains('open')) {
				closeDropdown(dropdown);
				return;
			}
			closeDropdowns(dropdown);
			dropdown.classList.add('open');
			menu.classList.remove('hidden');
			trigger.setAttribute('aria-expanded', 'true');
			const options = menu.querySelector('.custom-select-options');
			if (options) options.scrollTop = 0;
			syncDropdownScrollSpace();
			if (onOpen) onOpen();
			const search = menu.querySelector('input[type="search"]');
			if (search && !search.classList.contains('hidden')) search.focus();
		});
		menu.addEventListener('click', (event) => event.stopPropagation());
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
		const options = byId(dropdownId).querySelector('.custom-select-options');
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
			option.textContent = entry.label;
			option.addEventListener('click', () => onSelect(entry));
			options.appendChild(option);
		});
	}

	function currentBusy() {
		return busyOperations.size > 0;
	}

	function updateButtonStates() {
		const busy = currentBusy();
		byId('branch-update-btn').disabled = busy || !branchCanUpdate;
		byId('git-pull-btn').disabled = busy || !gitPullAllowed;
		byId('latest-release-btn').disabled = busy || !latestRelease ||
			(latestRelease.name === (updateData && updateData.version) && !isBleedingEdge);
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
			.forEach((button) => setVisible(button, hasBranchViews));
		if (!hasBranchViews && selectedDeveloperView !== 'source') {
			switchDeveloperView('source');
		}
		developerSubtabs.querySelectorAll('.branch-subtab').forEach((button) => {
			button.classList.toggle('active', button.dataset.developerView === selectedDeveloperView);
		});
		const activeBranchTitle = byId('active-branch-title');
		activeBranchTitle.replaceChildren();
		activeBranchTitle.appendChild(document.createTextNode(
			data.source_type === 'branch'
				? 'Active Branch: '
				: 'Installed Source: ',
		));
		if (data.source_type && data.source_name) {
			const sourceLink = document.createElement('a');
			sourceLink.href = '#';
			sourceLink.className = 'source-link';
			sourceLink.textContent = sourceLinkLabel(data.source_type, data.source_name);
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
		const inDeveloperMain = Boolean(
			branchUpdateParent.closest('#panel-developer') &&
			branchUpdateParent.id === 'developer-view-main',
		);
		setText('branch-updates-title', inDeveloperMain
			? 'Recent Main Updates'
			: 'Recent Branch Updates');
		setVisible(
			byId('branch-update-description'),
			branchUpdateParent.id === 'release-view-pre-release',
		);
		setText(
			'installed-commit',
			[
				localSha ? `${localSha.slice(0, 7)} (Current)` : 'unknown',
				...(hasBranchViews
					? [mainCommitSha
						? `${mainCommitSha.slice(0, 7)} (Last Main)`
						: 'unknown (Next update will fetch this)']
					: []),
			].join('\n'),
		);
		setText(
			'commit-date',
			[
				typeof data.commit_date === 'string' && data.commit_date
				? `${data.commit_date.replace('T', ' ').replace('Z', '')} UTC`
				: 'unknown (Next update will fetch this)',
				...(hasBranchViews
					? [typeof data.main_commit_date === 'string' && data.main_commit_date
						? `${data.main_commit_date.replace('T', ' ').replace('Z', '')} UTC`
						: 'unknown (Next update will fetch this)']
					: []),
			].join('\n'),
		);
		setText(
			'last-update-installed',
			data.installed_at
				? new Date(data.installed_at * 1000).toLocaleString()
				: 'Never (Perform update to record)',
		);

		const status = byId('branch-status');
		status.classList.remove('success', 'warning', 'error', 'rate-limit');
		branchCanUpdate = false;
		if (branchView === 'main' && hasBranchViews && !mainCommitSha) {
			if (remoteSha) {
				status.textContent = '';
			} else if (data.main_error === 'rate_limited') {
				status.textContent = MAIN_RATE_LIMIT_MESSAGE;
				status.classList.add('rate-limit');
			} else if (data.main_error === 'connection') {
				status.textContent = 'Could not reach GitHub. Check your connection and try again.';
				status.classList.add('error');
			} else if (data.main_error === 'not_found') {
				status.textContent = "Could not find branch 'main' on GitHub.";
				status.classList.add('warning');
			} else {
				status.textContent = 'GitHub could not verify the Main branch. Please try again later.';
				status.classList.add('warning');
			}
			setText('branch-update-btn', 'Update to Main Now');
			branchCanUpdate = Boolean(remoteSha);
		} else if (data.git_clone && active === 'detached') {
			status.textContent = 'Detached checkout. Pick a branch, release, tag, or PR from the other tabs.';
			status.classList.add('warning');
			setText('branch-update-btn', 'No Branch Checked Out');
		} else if (data.git_clone && gitInfo.dirty) {
			status.textContent = 'Local changes present. Commit, stash, or discard them before updating.';
			status.classList.add('warning');
			setText('branch-update-btn', 'Checkout Has Local Changes');
		} else if (!remoteSha) {
			if (viewData.error === 'rate_limited') {
				status.textContent = RATE_LIMIT_MESSAGE;
				status.classList.add('rate-limit');
			} else if (viewData.error === 'not_found') {
				if (branchView === 'main') {
					status.textContent = `Could not find branch '${viewData.targetName}' on GitHub.`;
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
			setText('branch-update-btn', data.git_clone
				? 'Branch Not on Ankimon Repository'
				: branchView === 'main' ? 'Update to Main Now' : 'Update Installed Source');
		} else if (data.git_clone && compareSha !== remoteSha && ['behind', 'diverged'].includes(relation)) {
			status.textContent = relation === 'behind'
				? `Your checkout is ahead of '${active}' on the Ankimon repository.`
				: `Your checkout has diverged from '${active}' on the Ankimon repository.`;
			status.classList.add('warning');
			setText('branch-update-btn', 'Cannot Fast-forward');
		} else if (compareSha !== remoteSha) {
			status.textContent = `New update available! (Latest: ${remoteSha.slice(0, 7)})`;
			status.classList.add('warning');
			setText('branch-update-btn', branchView === 'main'
				? 'Update to Main Now'
				: 'Update Installed Source');
			branchCanUpdate = true;
		} else {
			const noMainUpdatesForExternalSource = branchView === 'main' &&
				hasBranchViews && data.source_type &&
				!(data.source_type === 'branch' && data.source_name === 'main') &&
				viewData.commits.length === 0;
			if (noMainUpdatesForExternalSource) {
				status.textContent = 'Up to date!';
				status.classList.add('success');
				setText('branch-update-btn', 'Install Latest Main');
				branchCanUpdate = Boolean(remoteSha);
			} else {
				status.textContent = 'Up to date!';
				status.classList.add('success');
				setText('branch-update-btn', 'Already Up to Date');
			}
		}

		if (branchView === 'main') {
			status.classList.remove('success', 'warning', 'error', 'rate-limit', 'neutral');
			const requestError = data.main_error === 'rate_limited'
				? data.main_error
				: data.main_commits_error;
			if (requestError === 'rate_limited') {
				status.textContent = MAIN_RATE_LIMIT_MESSAGE;
				status.classList.add('rate-limit');
			} else if (!remoteSha) {
				if (data.main_error === 'connection') {
					status.textContent = 'Could not reach GitHub. Check your connection and try again.';
					status.classList.add('error');
				} else if (data.main_error === 'not_found') {
					status.textContent = "Could not find branch 'main' on GitHub.";
					status.classList.add('warning');
				} else {
					status.textContent = 'GitHub could not verify the Main branch. Please try again later.';
					status.classList.add('warning');
				}
			} else if (data.main_commits_error) {
				status.textContent = data.main_commits_error === 'connection'
					? 'Could not reach GitHub. Check your connection and try again.'
					: 'GitHub could not load Main updates. Please try again later.';
				status.classList.add(data.main_commits_error === 'connection' ? 'error' : 'warning');
			} else if (viewData.commits.length) {
				status.textContent = 'Update available!';
				status.classList.add('warning');
			} else {
				status.textContent = 'Up to date!';
				status.classList.add('success');
			}
		}

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
			button.classList.toggle('active', button.dataset.developerView === selectedDeveloperView);
		});
		document.querySelectorAll('#panel-developer .subtab-panel').forEach((panel) => {
			panel.classList.toggle('active', panel.id === `developer-view-${selectedDeveloperView}`);
		});
		setText('branch-updates-title', selectedDeveloperView === 'main'
			? 'Recent Main Updates'
			: 'Recent Branch Updates');
		setVisible(byId('branch-update-description'), false);
		if (selectedDeveloperView !== 'source') {
			byId(`developer-view-${selectedDeveloperView}`).appendChild(byId('branch-update-view'));
			if (updateData && updateData.show_branch_views) {
				selectedBranchView = selectedDeveloperView;
				renderBranch(updateData);
				syncBranchProgressVisibility();
			}
		}
		updateButtonStates();
	}

	function switchReleaseView(view) {
		selectedReleaseView = view === 'pre-release' ? 'pre-release' : 'releases';
		byId('release-subtabs').querySelectorAll('.branch-subtab').forEach((button) => {
			button.classList.toggle('active', button.dataset.releaseView === selectedReleaseView);
		});
		document.querySelectorAll('#panel-releases .subtab-panel').forEach((panel) => {
			panel.classList.toggle('active', panel.id === `release-view-${selectedReleaseView}`);
		});
		if (selectedReleaseView === 'pre-release') {
			byId('release-view-pre-release').appendChild(byId('branch-update-view'));
			selectedBranchView = 'main';
			setText('branch-updates-title', 'Recent Branch Updates');
			setVisible(byId('branch-update-description'), true);
			if (updateData) renderBranch(updateData);
			syncBranchProgressVisibility();
		}
	}

	function showDeveloperWarningIfNeeded() {
		if (!bridge) return;
		bridge.getDeveloperWarningHidden((hidden) => {
			if (hidden) return;
			const content = document.createElement('div');
			const warning = document.createElement('p');
			warning.className = 'warning-text modal-warning-text developer-warning-text';
			warning.textContent = 'This section contains developer tools and is usually for developer use. Installations made here are irreversible, and the code might not do as their description says. Pull requests and side branches may be experiments or work in progress. Please only proceed with sources you trust.';
			const row = document.createElement('label');
			row.className = 'check-row developer-warning-checkbox';
			const checkbox = document.createElement('input');
			checkbox.className = 'custom-checkbox';
			checkbox.type = 'checkbox';
			const indicator = document.createElement('span');
			indicator.className = 'checkbox-indicator';
			indicator.setAttribute('aria-hidden', 'true');
			const label = document.createElement('span');
			label.textContent = 'Do not show this again';
			row.append(checkbox, indicator, label);
			content.append(warning, row);
			showModal(
				'Developer Warning',
				content,
				'Got it',
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
		latestLabel.classList.remove('success', 'warning', 'error');
		isBleedingEdge = Boolean(updateData && updateData.bleeding_edge_release);
		lostReleaseCommits = updateData && Array.isArray(updateData.release_lost_commits)
			? updateData.release_lost_commits
			: [];
		if (error === 'rate_limited') {
			latestLabel.textContent = RATE_LIMIT_MESSAGE;
			latestLabel.classList.add('error');
		} else if (!latestRelease) {
			latestLabel.textContent = 'Could not check for updates.';
			latestLabel.classList.add('warning');
		} else if (isBleedingEdge) {
			latestLabel.textContent = `Switch back to ${latestRelease.name} from the pre-release`;
			latestLabel.classList.add('warning');
			setText('latest-release-btn', 'Switch to Latest Release');
		} else if (latestRelease.name === updateData.version) {
			latestLabel.textContent = `You're up to date (${latestRelease.name})`;
			latestLabel.classList.add('success');
			setText('latest-release-btn', 'Already Up to Date');
		} else {
			latestLabel.textContent = `New version available: ${latestRelease.name}`;
			latestLabel.classList.add('warning');
			setText('latest-release-btn', 'Update to Latest Release');
		}

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
		setDropdownLabel('release-select', selected ? selected.name : 'No releases found');
		renderOptions(
			'release-select',
			entries,
			selectedRelease,
			updateData && updateData.releases_error === 'rate_limited'
				? RATE_LIMIT_MESSAGE
				: 'No matching releases',
			(entry) => {
				selectedRelease = entry.value;
				setDropdownLabel('release-select', entry.label);
				closeDropdown(byId('release-select'));
				updateButtonStates();
			},
		);
	}

	function renderVersion(data) {
		const state = data.state || {};
		let version = '';
		if (state.source_type === 'branch' && state.source_name === 'main') {
			version = '| Pre-Release';
		} else if (state.source_type === 'branch') {
			const sha = typeof state.commit_sha === 'string' && state.commit_sha
				? ` (${state.commit_sha.slice(0, 7)})`
				: '';
			version = `(Branch: ${state.source_name}${sha})`;
		} else if (state.source_type === 'tag') {
			version = `(Tag: ${state.source_name})`;
		} else if (state.source_type === 'release' || state.source_type === 'pr') {
			version = '';
		} else if (data.experimental_build) {
			version = '(Experimental Build)';
		}
		const installed = byId('installed-version');
		installed.replaceChildren(document.createTextNode(`Installed: ${data.version}`));
		if (version) {
			const source = document.createElement('span');
			source.className = 'installed-source-detail';
			source.textContent = ` ${version}`;
			installed.appendChild(source);
		}
	}

	function renderSourceTypes() {
		const types = [
			{ value: 'pr', label: 'Pull Request' },
			{ value: 'branch', label: 'Side Branch' },
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
				prPage = 0;
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

	function renderDeveloperTargets(resetSelection = false) {
		if (!developerData) return;
		const type = selectedSourceType;
		const fields = byId('target-fields');
		const search = byId('pr-search');
		const label = byId('target-label');
		const pagination = byId('pr-pagination');
		const targetMenu = byId('source-target').querySelector('.custom-select-menu');
		const options = {
			pr: { label: 'Pull request:', entries: developerData.prs || [] },
			branch: {
				label: 'Side branch:',
				entries: (developerData.branches || []).filter((entry) => entry.name !== 'main'),
			},
			tag: { label: 'Tag:', entries: developerData.tags || [] },
		};
		pagination.classList.toggle('hidden', type !== 'pr');
		targetMenu.classList.toggle('pr-menu', type === 'pr');
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
		const pageCount = Math.max(1, Math.ceil(entries.length / PR_PAGE_SIZE));
		prPage = Math.min(prPage, pageCount - 1);
		const visibleEntries = type === 'pr'
			? entries.slice(prPage * PR_PAGE_SIZE, (prPage + 1) * PR_PAGE_SIZE)
			: entries;
		if (type === 'pr') {
			const first = visibleEntries[0];
			const last = visibleEntries[visibleEntries.length - 1];
			setText(
				'pr-page-summary',
				first && last
					? `Showing PR #${first.number} to #${last.number}`
					: 'Showing PR #— to #—',
			);
			byId('pr-page-prev').disabled = prPage === 0;
			byId('pr-page-next').disabled = prPage >= pageCount - 1;
		}
		const valueFor = (entry) => type === 'pr' ? String(entry.number) : entry.name;
		const labelFor = (entry) => type === 'pr'
			? `#${entry.number} — ${entry.title}`
			: entry.name;
		if (resetSelection || !options[type].entries.some((entry) => valueFor(entry) === selectedTarget)) {
			selectedTarget = type === 'branch' &&
				options[type].entries.some((entry) => entry.name === 'main')
				? 'main'
				: (options[type].entries[0] ? valueFor(options[type].entries[0]) : '');
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
		setText('source-rate-limit-status', rateLimited ? SOURCE_RATE_LIMIT_MESSAGE : '');
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
		if (type === 'pr') {
			byId('source-target').querySelector('.custom-select-options').scrollTop = 0;
		}
		updateButtonStates();
	}

	function switchTab(tab) {
		document.querySelectorAll('.update-tab-link').forEach((button) => {
			button.classList.toggle('active', button.dataset.tab === tab);
		});
		document.querySelectorAll('.tab-panel').forEach((panel) => {
			panel.classList.toggle('active', panel.id === `panel-${tab}`);
		});
		byId('github-link').classList.toggle('hidden', tab === 'sprites');
		if (tab === 'developer' && !developerData && !developerLoading && bridge) {
			developerLoading = true;
			developerLoadFailed = false;
			setDeveloperStatus('Loading developer options...');
			bridge.loadDeveloperData();
		}
		if (tab === 'developer') {
			switchDeveloperView(selectedDeveloperView);
			showDeveloperWarningIfNeeded();
		} else if (tab === 'releases') {
			switchReleaseView(selectedReleaseView);
		}
	}

	function startInstall(sourceType, sourceName, progressPanel) {
		activeProgressPanel = progressPanel;
		activeProgressBranchView = progressPanel === 'branch' &&
			updateData && updateData.show_branch_views
			? selectedBranchView
			: 'current';
		setOperationProgress(0, true, true);
		setOperationStatus('', false);
		bridge.installUpdate(sourceType, sourceName, (result) => {
			if (result && result.success === false) {
				setOperationProgress(0, true);
				if (activeProgressPanel) {
					setProgressMessage(
						byId(`${activeProgressPanel}-progress`)
							.querySelector('.progress-label span'),
						result.message || 'Update could not be started.',
						'failure',
					);
				}
			}
		});
	}

	function install(sourceType, sourceName, _label, warning = '', progressPanel = 'developer') {
		const prompt = updateData && updateData.git_clone
			? `Switch this Git checkout to `
			: `Update Ankimon to `;
		const resolvedTarget = sourceType === 'installed' && updateData
			? { type: updateData.source_type, name: updateData.source_name }
			: sourceType === 'current' && updateData
				? { type: 'branch', name: updateData.git_info && updateData.git_info.branch }
				: { type: sourceType, name: sourceName };
		if (!warning && resolvedTarget.type === 'pr') {
			warning = 'Anyone can open a pull request; this code is unreviewed and unreleased. Installing it runs unverified code on your computer. It may or may not behave as expected, and can even break the add-on. Only proceed if you trust this PR. Use at your own risk.';
		} else if (!warning && resolvedTarget.type === 'branch' &&
			resolvedTarget.name && resolvedTarget.name !== 'main') {
			warning = 'Unlike main, these are collaborator branches and can be unreleased. The branch may be a work in progress. They may or may not behave as expected, and might even break the add-on. Only proceed if you trust this branch. Use at your own risk.';
		}
		const linkTarget = sourceLinkTarget(sourceType, sourceName);
		const fragment = document.createDocumentFragment();
		if (warning) {
			const warningElement = document.createElement('p');
			warningElement.className = 'warning-text modal-warning-text';
			const heading = document.createElement('strong');
			heading.textContent = 'Warning: ';
			warningElement.append(heading, document.createTextNode(warning));
			fragment.append(warningElement, document.createTextNode('\n'));
		}
		fragment.appendChild(document.createTextNode(prompt));
		const link = document.createElement('a');
		link.href = '#';
		link.className = 'source-link';
		link.textContent = sourceLinkLabel(linkTarget.sourceType, linkTarget.sourceName);
		link.addEventListener('click', (event) => {
			event.preventDefault();
			if (bridge) {
				bridge.openGitHubTarget(
					sourceUrl(linkTarget.sourceType, linkTarget.sourceName),
					(opened) => {
						if (!opened) {
							showToast('Could not open the selected source in your browser.', true);
						}
					},
				);
			}
		});
		fragment.appendChild(link);
		if (updateData && updateData.git_clone) {
			fragment.appendChild(document.createTextNode('?\n\nYour local branches and commits will not be reset. The checkout must be clean, and Anki must be restarted afterward.'));
		} else {
			fragment.appendChild(document.createTextNode('?\n\nYour Pokémon data, settings, and sprites will be preserved.'));
		}
		showModal(
			'Confirm Update',
			fragment,
			'Install',
			() => startInstall(sourceType, sourceName, progressPanel),
			true,
			Boolean(warning),
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
			'Switch to Latest Release',
			() => startInstall('release', releaseName, 'releases'),
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
		if (selectedBranchView === 'main') {
			install('branch', 'main', 'latest main', '', 'branch');
			return;
		}
		if (updateData.show_branch_views) {
			install('installed', 'current', `latest installed source (${updateData.active_branch})`, '', 'branch');
			return;
		}
		const name = updateData.active_branch || 'main';
		if (updateData.git_clone) {
			install('current', 'current', name, '', 'branch');
		} else {
			install('branch', updateData.source_name || 'main', `latest ${name}`, '', 'branch');
		}
	}

	window.onUpdateData = (data) => {
		updateData = data;
		spriteCheckUpToDate = Boolean(data.sprite_check_up_to_date);
		renderVersion(data);
		renderBranch(data);
		renderReleases(data.releases, data.releases_error);
		switchDeveloperView(selectedDeveloperView);
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

	window.onDeveloperLoadFailed = (data) => {
		developerLoading = false;
		developerLoadFailed = true;
		setDeveloperStatus(`Could not load developer options: ${data.message}`);
	};

	window.onUpdateLoadFailed = (data) => {
		setOperationStatus(`Could not check for updates: ${data.message}`);
	};

	window.onUpdateCodeError = (data) => {
		setCodeError(data.message, data.traceback);
	};

	window.onUpdateOperation = (data) => {
		if (data.busy) busyOperations.add(data.kind);
		else busyOperations.delete(data.kind);
		if (data.busy && data.kind === 'load') setOperationStatus('Checking for updates...');
		if (data.busy && data.kind === 'developer') {
			developerLoading = true;
			developerLoadFailed = false;
			setDeveloperStatus('Loading developer options...');
		}
		if (!data.busy && data.kind === 'developer' && !developerData && !developerLoadFailed) {
			developerLoading = false;
			setDeveloperStatus('');
		}
		if (data.busy && data.kind === 'sprites-check') {
			setSpriteProgress(0, 'Checking for sprite updates...');
			byId('sprites-progress').classList.remove('hidden');
		}
		if (data.busy && data.kind === 'sprites-install') {
			byId('sprites-progress').classList.remove('hidden');
			setSpriteProgress(0, 'Updating sprites...');
		}
		if (!data.busy && data.kind === 'load' && !currentBusy()) setOperationStatus('', false);
		updateButtonStates();
	};

	window.onUpdateInstallStarted = (data) => {
		setOperationStatus('', false);
		if (activeProgressPanel) {
			setProgressMessage(
				byId(`${activeProgressPanel}-progress`).querySelector('.progress-label span'),
				data.label ? `Downloading ${data.label}...` : 'Preparing update...',
			);
		}
		setOperationProgress(0, true, !updateData || !updateData.git_clone);
	};

	window.onUpdateProgress = (data) => setOperationProgress(data.percent, true);
	window.onUpdateStatus = (data) => {
		setOperationStatus('', false);
		if (activeProgressPanel) {
			setProgressMessage(
				byId(`${activeProgressPanel}-progress`).querySelector('.progress-label span'),
				data.message,
			);
		}
	};
	window.onSpritesCheckProgress = (data) => {
		setSpriteProgress(data.percent, data.message || 'Checking for sprite updates...');
	};

	window.onUpdateFinished = (data) => {
		const progressMessage = data.success
			? `Update complete! ${(data.status || data.message || '')
				.replace(/^Update complete!?\s*/i, '')}`.trim()
			: (data.message || 'Update failed.');
		const progressLabel = activeProgressPanel
			? byId(`${activeProgressPanel}-progress`).querySelector('.progress-label span')
			: null;
		if (progressLabel) {
			setProgressMessage(
				progressLabel,
				progressMessage,
				data.success ? 'complete' : 'failure',
			);
		}
		setOperationStatus('', false);
		setOperationProgress(data.success ? 100 : 0, true);
		if (data.success) {
			if (bridge) bridge.loadData();
			showModal(
				'Update Complete',
				'Update applied successfully. Please restart Anki for changes to take effect.',
				'OK',
				null,
				false,
			);
		} else {
			showModal('Update Failed', data.message || 'The update failed.', 'Close', null, false);
		}
	};

	window.onSpritesChecked = (result) => {
		let message = '';
		let outcome = '';
		byId('sprites-install-btn').classList.add('hidden');
		spriteUpdateAvailable = result.status === 'update_available';
		spriteCheckUpToDate = result.status === 'up_to_date';
		if (result.status === 'up_to_date') {
			message = 'Sprites are already up to date!';
			outcome = 'sprite-success';
		} else if (result.status === 'error') {
			message = result.rate_limited
				? RATE_LIMIT_MESSAGE
				: `Error checking updates: ${result.error}`;
			outcome = result.rate_limited ? 'rate-limit' : 'failure';
		} else if (spriteUpdateAvailable) {
			message = `A sprites update is available!\n\n  • New sprites: ${(result.added || []).length}\n  • Modified sprites: ${(result.modified || []).length}`;
			if ((result.deleted || []).length) message += `\n  • Obsolete to remove: ${result.deleted.length}`;
			outcome = 'sprite-warning';
			byId('sprites-install-btn').classList.remove('hidden');
		}
		setSpriteProgress(100, message, outcome);
		updateButtonStates();
	};

	window.onSpritesCheckFailed = (data) => {
		const rateLimited = data.rate_limited;
		setSpriteProgress(100, rateLimited
			? RATE_LIMIT_MESSAGE
			: `Error checking sprite updates: ${data.message}`,
		rateLimited ? 'rate-limit' : 'failure');
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
			? `Update complete! ${data.message}`
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
	const updateTabNames = ['releases', 'sprites', 'developer'];
	document.querySelector('.sidebar-nav').addEventListener('wheel', (event) => {
		const tab = event.target.closest('.update-tab-link');
		if (!tab || !event.deltaY) return;
		const activeTab = document.querySelector('.update-tab-link.active');
		const tabIndex = updateTabNames.indexOf(activeTab && activeTab.dataset.tab);
		if (tabIndex < 0) return;
		event.preventDefault();
		event.stopPropagation();
		const nextIndex = Math.max(
			0,
			Math.min(updateTabNames.length - 1, tabIndex + (event.deltaY > 0 ? 1 : -1)),
		);
		if (nextIndex !== tabIndex) switchTab(updateTabNames[nextIndex]);
	}, { passive: false });

	byId('developer-subtabs').querySelectorAll('[data-developer-view]').forEach((button) => {
		button.addEventListener('click', () => switchDeveloperView(button.dataset.developerView));
	});
	byId('developer-subtabs').addEventListener('wheel', (event) => {
		if (!event.deltaY) return;
		event.preventDefault();
		event.stopPropagation();
		const views = updateData && updateData.show_branch_views
			? ['source', 'main', 'current']
			: ['source'];
		const currentIndex = views.indexOf(selectedDeveloperView);
		const nextIndex = Math.max(
			0,
			Math.min(views.length - 1, currentIndex + (event.deltaY < 0 ? 1 : -1)),
		);
		if (nextIndex !== currentIndex) switchDeveloperView(views[nextIndex]);
	}, { passive: false });
	byId('release-subtabs').querySelectorAll('[data-release-view]').forEach((button) => {
		button.addEventListener('click', () => switchReleaseView(button.dataset.releaseView));
	});
	byId('release-subtabs').addEventListener('wheel', (event) => {
		if (!event.deltaY) return;
		event.preventDefault();
		event.stopPropagation();
		const nextView = event.deltaY > 0 ? 'pre-release' : 'releases';
		switchReleaseView(nextView);
	}, { passive: false });

	document.querySelectorAll('.custom-select').forEach((dropdown) => wireDropdown(dropdown));
	document.addEventListener('click', () => closeDropdowns());
	document.addEventListener('keydown', (event) => {
		if (event.key === 'Escape') closeDropdowns();
	});
	renderSourceTypes();
	byId('release-select').querySelector('.custom-select-search')
		.addEventListener('input', renderReleaseOptions);
	byId('pr-search').addEventListener('input', () => {
		prPage = 0;
		renderDeveloperTargets(false);
	});
	byId('pr-page-prev').addEventListener('click', () => {
		if (prPage > 0) {
			prPage -= 1;
			renderDeveloperTargets(false);
		}
	});
	byId('pr-page-next').addEventListener('click', () => {
		if (!byId('pr-page-next').disabled) {
			prPage += 1;
			renderDeveloperTargets(false);
		}
	});
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
		install('current', 'current', branch || 'current Git branch', '', 'branch');
	});
	byId('latest-release-btn').addEventListener('click', () => {
		if (latestRelease) {
			if (isBleedingEdge) {
				showReleaseSwitchConfirmation();
			} else {
				install('release', latestRelease.name, `latest release (${latestRelease.name})`, '', 'releases');
			}
		}
	});
	byId('release-install-btn').addEventListener('click', () => {
		const release = updateData && (updateData.releases || []).find(
			(item) => item.name === selectedRelease,
		);
		if (release) install('release', release.name, `release ${release.name}`, '', 'releases');
	});

	byId('developer-install-btn').addEventListener('click', () => {
		const source = selectedSourceType;
		const value = selectedTarget;
		if (!value || !developerData) return;
		const entry = source === 'pr'
			? (developerData.prs || []).find((pr) => String(pr.number) === value)
			: source === 'branch'
				? (developerData.branches || []).find((branch) => branch.name === value)
				: (developerData.tags || []).find((tag) => tag.name === value);
		if (!entry) return;
		install(
			source,
			value,
			source === 'pr' ? `PR #${entry.number} (${entry.title})` : `${source} ${value}`,
			'',
			'developer',
		);
	});

	byId('sprites-check-btn').addEventListener('click', () => {
		if (!bridge || spriteCheckUpToDate) return;
		spriteUpdateAvailable = false;
		byId('sprites-install-btn').classList.add('hidden');
		setSpriteProgress(0, 'Checking for sprite updates...');
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
