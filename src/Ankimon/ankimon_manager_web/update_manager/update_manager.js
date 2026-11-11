(() => {
	'use strict';

	let bridge = null;
	let navBridge = null;
	let updateData = null;
	let developerData = null;
	let developerLoading = false;
	let developerLoadFailed = false;
	let selectedSourceType = 'main';
	let selectedTarget = '';
	let selectedRelease = '';
	let latestRelease = null;
	let branchCanUpdate = false;
	let gitPullAllowed = false;
	let spriteUpdateAvailable = false;
	let spriteCheckUpToDate = false;
	let activeProgressPanel = '';
	const busyOperations = new Set();
	let toastTimer = null;

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

	function showModal(title, message, confirmLabel, onConfirm, showCancel = true) {
		setText('modal-title', title);
		const messageElement = byId('modal-message');
		messageElement.replaceChildren();
		if (message instanceof Node) messageElement.appendChild(message);
		else messageElement.textContent = message;
		setText('modal-confirm', confirmLabel);
		setVisible(byId('modal-cancel'), showCancel);
		const modal = byId('update-modal');
		modal.classList.remove('hidden');
		const confirm = byId('modal-confirm');
		const cancel = byId('modal-cancel');
		const previous = document.activeElement;
		const onKey = (event) => { if (event.key === 'Escape') close(); };
		const close = () => {
			modal.classList.add('hidden');
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
		setVisible(container, visible);
	}

	function setSpriteProgress(containerId, value, message = null) {
		const container = byId(containerId);
		const progress = container.querySelector('progress');
		const percent = Math.max(0, Math.min(100, Number(value) || 0));
		progress.value = percent;
		container.querySelector('output').textContent = `${percent}%`;
		if (message) container.querySelector('.progress-label span').textContent = message;
	}

	function setDeveloperStatus(message) {
		setText('developer-status', message || '');
		setVisible(byId('developer-status'), Boolean(message));
	}

	function closeDropdown(dropdown) {
		dropdown.classList.remove('open');
		dropdown.querySelector('.custom-select-menu').classList.add('hidden');
		dropdown.querySelector('.custom-select-trigger').setAttribute('aria-expanded', 'false');
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
			if (onOpen) onOpen();
			const search = menu.querySelector('input[type="search"]');
			if (search && !search.classList.contains('hidden')) search.focus();
		});
		menu.addEventListener('click', (event) => event.stopPropagation());
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
			latestRelease.name === (updateData && updateData.version);
		byId('release-install-btn').disabled = busy || !selectedRelease;
		byId('developer-install-btn').disabled = busy ||
			(selectedSourceType !== 'main' && !selectedTarget);
		byId('sprites-check-btn').disabled = busy || spriteCheckUpToDate;
		byId('sprites-install-btn').disabled = busy || !spriteUpdateAvailable;
	}

	function renderBranch(data) {
		const state = data.state || {};
		const gitInfo = data.git_info || {};
		const active = data.active_branch || 'main';
		const localSha = typeof data.local_sha === 'string' ? data.local_sha : '';
		const remoteSha = typeof data.remote_sha === 'string' ? data.remote_sha : '';
		const relation = data.relation || state.git_relation || null;
		setText('active-branch-title', `Active Branch: ${active}`);
		setText('installed-commit', localSha ? localSha.slice(0, 7) : 'unknown');
		setText(
			'commit-date',
			typeof data.commit_date === 'string' && data.commit_date
				? `${data.commit_date.replace('T', ' ').replace('Z', '')} UTC`
				: 'Unknown (first update will fetch this)',
		);
		setText(
			'last-update-installed',
			data.installed_at
				? new Date(data.installed_at * 1000).toLocaleString()
				: 'Never (Perform update to record)',
		);

		const status = byId('branch-status');
		status.classList.remove('success', 'warning', 'error');
		branchCanUpdate = false;
		if (data.git_clone && active === 'detached') {
			status.textContent = 'Status: Detached checkout. Pick a branch, release, tag, or PR from the other tabs.';
			status.classList.add('warning');
			setText('branch-update-btn', 'No Branch Checked Out');
		} else if (data.git_clone && gitInfo.dirty) {
			status.textContent = 'Status: Local changes present. Commit, stash, or discard them before updating.';
			status.classList.add('warning');
			setText('branch-update-btn', 'Checkout Has Local Changes');
		} else if (!remoteSha) {
			status.textContent = data.git_clone
				? `Status: Branch '${data.query_branch || active}' was not found on the Ankimon repository (or it could not be reached).`
				: 'Status: Could not check connection.';
			status.classList.add('error');
			setText('branch-update-btn', data.git_clone ? 'Branch Not on Ankimon Repository' : 'Update Branch Now');
		} else if (data.git_clone && localSha !== remoteSha && ['behind', 'diverged'].includes(relation)) {
			status.textContent = relation === 'behind'
				? `Status: Your checkout is ahead of '${active}' on the Ankimon repository.`
				: `Status: Your checkout has diverged from '${active}' on the Ankimon repository.`;
			status.classList.add('warning');
			setText('branch-update-btn', 'Cannot Fast-forward');
		} else if (localSha !== remoteSha) {
			status.textContent = `Status: New Update Available! (Latest: ${remoteSha.slice(0, 7)})`;
			status.classList.add('warning');
			setText('branch-update-btn', 'Update Branch Now');
			branchCanUpdate = true;
		} else {
			status.textContent = 'Status: Up to date!';
			status.classList.add('success');
			setText('branch-update-btn', 'Already Up to Date');
		}

		const commits = byId('recent-commits');
		commits.replaceChildren();
		if (data.commits && data.commits.length) {
			data.commits.forEach((commit) => {
				const item = document.createElement('li');
				const sha = document.createElement('code');
				sha.textContent = commit.sha || '';
				item.append(sha, document.createTextNode(commit.message || ''));
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

		if (data.git_clone) {
			const branch = gitInfo.branch || 'unknown';
			const sha = gitInfo.sha || 'unknown';
			setText(
				'git-info',
				`${branch === 'HEAD' ? 'detached checkout' : branch} · ${sha} · ${gitInfo.dirty ? 'local changes' : 'clean'}`,
			);
			byId('git-notice').classList.remove('hidden');
		} else {
			byId('git-notice').classList.add('hidden');
		}
	}

	function renderReleases(releases) {
		const list = Array.isArray(releases) ? releases : [];
		latestRelease = list[0] || null;
		const latestLabel = byId('latest-release-label');
		latestLabel.classList.remove('success', 'warning', 'error');
		if (!latestRelease) {
			latestLabel.textContent = 'Could not check for updates.';
			latestLabel.classList.add('error');
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
			'No matching releases',
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
		if (state.source_type === 'branch') {
			const sha = typeof state.commit_sha === 'string' && state.commit_sha
				? ` (${state.commit_sha.slice(0, 7)})`
				: '';
			version = `(Branch: ${state.source_name}${sha})`;
		} else if (state.source_type === 'pr') {
			const sha = typeof state.commit_sha === 'string' && state.commit_sha
				? ` (${state.commit_sha.slice(0, 7)})`
				: '';
			version = `(PR #${state.source_name}${sha})`;
		} else if (state.source_type === 'tag') {
			version = `(Tag: ${state.source_name})`;
		} else if (state.source_type === 'release') {
			version = `(Release: ${state.source_name})`;
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
			{ value: 'main', label: 'Latest Main Branch' },
			{ value: 'pr', label: 'Pull Request' },
			{ value: 'branch', label: 'Branch' },
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
		const options = {
			pr: { label: 'Pull request:', entries: developerData.prs || [] },
			branch: { label: 'Branch:', entries: developerData.branches || [] },
			tag: { label: 'Tag:', entries: developerData.tags || [] },
		};
		if (type === 'main') {
			fields.classList.add('hidden');
			selectedTarget = '';
			updateButtonStates();
			return;
		}
		fields.classList.remove('hidden');
		label.textContent = options[type].label;
		search.placeholder = type === 'pr'
			? 'Search pull requests by number or title'
			: `Search ${type === 'branch' ? 'branches' : 'tags'}`;
		search.setAttribute('aria-label', `Search ${type === 'pr' ? 'pull requests' : `${type}s`}`);
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
		setDropdownLabel('source-target', selectedEntry ? labelFor(selectedEntry) : 'No sources found');
		renderOptions(
			'source-target',
			entries.map((entry) => ({ value: valueFor(entry), label: labelFor(entry) })),
			selectedTarget,
			type === 'pr' ? 'No matching open pull requests' :
				(type === 'branch' ? 'No matching branches' : 'No matching tags'),
			(entry) => {
				selectedTarget = entry.value;
				setDropdownLabel('source-target', entry.label);
				closeDropdown(byId('source-target'));
				updateButtonStates();
			},
		);
		updateButtonStates();
	}

	function switchTab(tab) {
		document.querySelectorAll('.update-tab, .update-tab-link').forEach((button) => {
			button.classList.toggle('active', button.dataset.tab === tab);
		});
		document.querySelectorAll('.tab-panel').forEach((panel) => {
			panel.classList.toggle('active', panel.id === `panel-${tab}`);
		});
		if (tab === 'developer' && !developerData && !developerLoading && bridge) {
			developerLoading = true;
			developerLoadFailed = false;
			setDeveloperStatus('Loading developer options...');
			bridge.loadDeveloperData();
		}
	}

	function install(sourceType, sourceName, label, warning = '', progressPanel = 'developer') {
		const prompt = updateData && updateData.git_clone
			? `Switch this Git checkout to `
			: `Update Ankimon to `;
		const fragment = document.createDocumentFragment();
		if (warning) {
			fragment.appendChild(document.createTextNode(`${warning}\n\n`));
		}
		fragment.appendChild(document.createTextNode(prompt));
		const link = document.createElement('a');
		link.href = '#';
		link.className = 'source-link';
		link.textContent = label;
		link.addEventListener('click', (event) => {
			event.preventDefault();
			if (bridge) {
				bridge.openGitHubTarget(sourceUrl(sourceType, sourceName), (opened) => {
					if (!opened) showToast('Could not open the selected source in your browser.', true);
				});
			}
		});
		fragment.appendChild(link);
		if (updateData && updateData.git_clone) {
			fragment.appendChild(document.createTextNode('?\n\nYour local branches and commits will not be reset. The checkout must be clean, and Anki must be restarted afterward.'));
		} else {
			fragment.appendChild(document.createTextNode('?\n\nYour Pokémon data, settings, and sprites will be preserved.'));
		}
		showModal('Confirm Update', fragment, 'Install', () => {
			activeProgressPanel = progressPanel;
			setOperationProgress(0, true, true);
			setOperationStatus(updateData && updateData.git_clone
				? `Preparing Git checkout for ${label}...`
				: `Downloading ${label}...`);
			bridge.installUpdate(sourceType, sourceName, (result) => {
				if (result && result.success === false) {
					setOperationProgress(0, false);
					setOperationStatus(result.message || 'Update could not be started.');
				}
			});
		});
	}

	function sourceUrl(sourceType, sourceName) {
		const name = sourceType === 'current' && updateData && updateData.git_info
			? updateData.git_info.branch
			: sourceName;
		const ref = (name || 'main').split('/').map(encodeURIComponent).join('/');
		if (sourceType === 'pr') return `https://github.com/h0tp-ftw/ankimon/pull/${ref}`;
		if (sourceType === 'release') return `https://github.com/h0tp-ftw/ankimon/releases/tag/${ref}`;
		if (sourceType === 'tag') return `https://github.com/h0tp-ftw/ankimon/tree/${ref}`;
		return `https://github.com/h0tp-ftw/ankimon/tree/${ref}`;
	}

	function installActiveBranch() {
		if (!updateData) return;
		const name = updateData.active_branch || 'main';
		if (updateData.git_clone) {
			install('current', 'current', name, '', 'branch');
		} else {
			install('branch', name, `latest ${name}`, '', 'branch');
		}
	}

	window.onUpdateData = (data) => {
		updateData = data;
		spriteCheckUpToDate = Boolean(data.sprite_check_up_to_date);
		renderVersion(data);
		renderBranch(data);
		renderReleases(data.releases);
		byId('branch-snooze').checked = Number(data.skip_until) > Date.now() / 1000;
		byId('sprites-snooze').checked = Boolean(data.sprite_snoozed);
		updateButtonStates();
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
			const status = byId('sprites-status');
			status.classList.remove('success', 'warning', 'error');
			status.textContent = 'Checking for sprite updates...';
			byId('sprites-progress').classList.add('hidden');
			setSpriteProgress('sprites-check-progress', 0, 'Checking sprites...');
			byId('sprites-check-progress').classList.remove('hidden');
		}
		if (data.busy && data.kind === 'sprites-install') {
			byId('sprites-status').classList.remove('success', 'warning', 'error');
			byId('sprites-check-progress').classList.add('hidden');
			byId('sprites-progress').classList.remove('hidden');
			setSpriteProgress('sprites-progress', 0, 'Updating sprites...');
		}
		if (!data.busy && data.kind === 'load' && !currentBusy()) setOperationStatus('', false);
		updateButtonStates();
	};

	window.onUpdateInstallStarted = (data) => {
		setOperationStatus(updateData && updateData.git_clone
			? `Preparing Git checkout for ${data.label}...`
			: `Downloading ${data.label}...`);
		if (activeProgressPanel) {
			byId(`${activeProgressPanel}-progress`).querySelector('.progress-label span').textContent =
				data.label ? `Downloading ${data.label}...` : 'Preparing update...';
		}
		setOperationProgress(0, true, !updateData || !updateData.git_clone);
	};

	window.onUpdateProgress = (data) => setOperationProgress(data.percent, true);
	window.onUpdateStatus = (data) => {
		setOperationStatus(data.message);
		if (activeProgressPanel) {
			byId(`${activeProgressPanel}-progress`).querySelector('.progress-label span').textContent =
				data.message;
		}
	};
	window.onSpritesCheckProgress = (data) => {
		setSpriteProgress('sprites-check-progress', data.percent, data.message);
		setText('sprites-status', data.message || 'Checking for sprite updates...');
	};

	window.onUpdateFinished = (data) => {
		setOperationProgress(data.success ? 100 : 0, Boolean(data.success));
		setOperationStatus(data.status || data.message, true);
		if (data.success) {
			showModal(
				'Update Complete',
				'Update applied successfully. Please restart Anki for changes to take effect.',
				'OK',
				null,
				false,
			);
		} else {
			setOperationProgress(0, false);
			showModal('Update Failed', data.message || 'The update failed.', 'Close', null, false);
		}
	};

	window.onSpritesChecked = (result) => {
		const status = byId('sprites-status');
		status.classList.remove('success', 'warning', 'error');
		setSpriteProgress('sprites-check-progress', 100, 'Sprite check complete.');
		byId('sprites-install-btn').classList.add('hidden');
		spriteUpdateAvailable = result.status === 'update_available';
		spriteCheckUpToDate = result.status === 'up_to_date';
		if (result.status === 'up_to_date') {
			status.textContent = 'Sprites are already up to date!';
			status.classList.add('success');
		} else if (result.status === 'error') {
			status.textContent = `Error checking updates: ${result.error}`;
			status.classList.add('error');
		} else if (spriteUpdateAvailable) {
			let message = `A sprites update is available!\n\n  • New sprites: ${(result.added || []).length}\n  • Modified sprites: ${(result.modified || []).length}`;
			if ((result.deleted || []).length) message += `\n  • Obsolete to remove: ${result.deleted.length}`;
			status.textContent = message;
			status.classList.add('warning');
			byId('sprites-install-btn').classList.remove('hidden');
		}
		updateButtonStates();
	};

	window.onSpritesCheckFailed = (data) => {
		setText('sprites-status', `Error checking sprite updates: ${data.message}`);
		byId('sprites-status').classList.remove('success', 'warning');
		byId('sprites-status').classList.add('error');
		setSpriteProgress('sprites-check-progress', 100, 'Sprite check failed.');
		spriteCheckUpToDate = false;
	};

	window.onSpriteProgress = (data) => {
		setSpriteProgress('sprites-progress', data.percent);
	};

	window.onSpriteStatus = (data) => {
		setText('sprites-status', data.message);
		byId('sprites-progress').querySelector('.progress-label span').textContent = data.message;
	};

	window.onSpritesFinished = (data) => {
		spriteUpdateAvailable = false;
		byId('sprites-install-btn').classList.add('hidden');
		if (data.success) setSpriteProgress('sprites-progress', 100);
		setText('sprites-status', data.success
			? `Update complete! ${data.message}`
			: `Update failed: ${data.message}`);
		byId('sprites-status').classList.remove('warning', 'success', 'error');
		byId('sprites-status').classList.toggle('success', data.success);
		byId('sprites-status').classList.toggle('error', !data.success);
	};

	window.refreshUpdateManager = () => {
		if (bridge) bridge.loadData();
	};

	byId('close-btn').addEventListener('click', () => {
		if (navBridge) navBridge.closeWindow();
	});

	document.querySelectorAll('.update-tab, .update-tab-link').forEach((button) => {
		button.addEventListener('click', () => switchTab(button.dataset.tab));
	});
	byId('update-tabs').addEventListener('wheel', (event) => {
		if (Math.abs(event.deltaY) <= Math.abs(event.deltaX)) return;
		const tabs = ['branch', 'releases', 'developer', 'sprites'];
		const active = tabs.indexOf(
			document.querySelector('.update-tab.active').dataset.tab,
		);
		const next = event.deltaY < 0 ? Math.min(active + 1, tabs.length - 1) :
			Math.max(active - 1, 0);
		if (next !== active) {
			event.preventDefault();
			switchTab(tabs[next]);
		}
	}, { passive: false });

	document.querySelectorAll('.custom-select').forEach((dropdown) => wireDropdown(dropdown));
	document.addEventListener('click', () => closeDropdowns());
	document.addEventListener('keydown', (event) => {
		if (event.key === 'Escape') closeDropdowns();
	});
	renderSourceTypes();
	byId('release-select').querySelector('.custom-select-search')
		.addEventListener('input', renderReleaseOptions);
	byId('pr-search').addEventListener('input', () => renderDeveloperTargets(false));
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
			install('release', latestRelease.name, `latest release (${latestRelease.name})`, '', 'releases');
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
		if (source === 'main') {
			install('branch', 'main', 'latest main', '', 'developer');
			return;
		}
		const value = selectedTarget;
		if (!value || !developerData) return;
		const entry = source === 'pr'
			? (developerData.prs || []).find((pr) => String(pr.number) === value)
			: source === 'branch'
				? (developerData.branches || []).find((branch) => branch.name === value)
				: (developerData.tags || []).find((tag) => tag.name === value);
		if (!entry) return;
		const warning = source === 'pr'
			? 'WARNING: Anyone can open a pull request. This code is unreviewed and unreleased — installing it runs unverified code on your computer. Only proceed if you trust this PR. Use at your own risk.'
			: '';
		install(
			source,
			value,
			source === 'pr' ? `PR #${entry.number} (${entry.title})` : `${source} ${value}`,
			warning,
			'developer',
		);
	});

	byId('sprites-check-btn').addEventListener('click', () => {
		if (!bridge || spriteCheckUpToDate) return;
		spriteUpdateAvailable = false;
		byId('sprites-install-btn').classList.add('hidden');
		byId('sprites-progress').classList.add('hidden');
		setSpriteProgress('sprites-check-progress', 0, 'Checking sprites...');
		byId('sprites-check-progress').classList.remove('hidden');
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
