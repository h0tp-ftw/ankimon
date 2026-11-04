(() => {
	'use strict';

	let bridge = null;
	let navBridge = null;
	let updateData = null;
	let developerData = null;
	let developerLoading = false;
	let latestRelease = null;
	let branchCanUpdate = false;
	let gitPullAllowed = false;
	let spriteUpdateAvailable = false;
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
		setText('modal-message', message);
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

	function setOperationProgress(value, visible = true) {
		const progress = byId('operation-progress');
		progress.value = Math.max(0, Math.min(100, Number(value) || 0));
		setVisible(progress, visible);
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
		byId('release-install-btn').disabled = busy || !byId('release-select').value;
		byId('developer-install-btn').disabled = busy ||
			(byId('source-type').value !== 'main' && !byId('source-target').value);
		byId('sprites-check-btn').disabled = busy;
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

		const select = byId('release-select');
		select.replaceChildren();
		list.forEach((release) => {
			const option = document.createElement('option');
			option.value = release.name;
			option.textContent = release.name;
			select.appendChild(option);
		});
		if (!list.length) {
			const option = document.createElement('option');
			option.value = '';
			option.textContent = 'No releases found';
			select.appendChild(option);
		}
	}

	function renderVersion(data) {
		const state = data.state || {};
		let version = `Installed: ${data.version}`;
		if (state.source_type === 'branch') {
			const sha = typeof state.commit_sha === 'string' && state.commit_sha
				? ` (${state.commit_sha.slice(0, 7)})`
				: '';
			version += ` (Branch: ${state.source_name}${sha})`;
		} else if (state.source_type === 'pr') {
			const sha = typeof state.commit_sha === 'string' && state.commit_sha
				? ` (${state.commit_sha.slice(0, 7)})`
				: '';
			version += ` (PR #${state.source_name}${sha})`;
		} else if (state.source_type === 'tag') {
			version += ` (Tag: ${state.source_name})`;
		} else if (state.source_type === 'release') {
			version += ` (Release: ${state.source_name})`;
		} else if (data.experimental_build) {
			version += ' (Experimental Build)';
		}
		setText('installed-version', version);
	}

	function renderDeveloperTargets(resetSelection = false) {
		if (!developerData) return;
		const type = byId('source-type').value;
		const fields = byId('target-fields');
		const search = byId('pr-search');
		const select = byId('source-target');
		const label = byId('target-label');
		const options = {
			pr: { label: 'Pull request:', entries: developerData.prs || [] },
			branch: { label: 'Branch:', entries: developerData.branches || [] },
			tag: { label: 'Tag:', entries: developerData.tags || [] },
		};
		if (type === 'main') {
			fields.classList.add('hidden');
			select.replaceChildren();
			updateButtonStates();
			return;
		}
		fields.classList.remove('hidden');
		label.textContent = options[type].label;
		search.classList.toggle('hidden', type !== 'pr');
		const query = type === 'pr' ? search.value.trim().toLocaleLowerCase() : '';
		const numberQuery = query.startsWith('#') ? query.slice(1) : query;
		const entries = options[type].entries.filter((entry) => {
			if (type !== 'pr' || !query) return true;
			return String(entry.number).toLocaleLowerCase().includes(numberQuery) ||
				String(entry.title || '').toLocaleLowerCase().includes(query);
		});
		const selected = resetSelection ? '' : select.value;
		select.replaceChildren();
		entries.forEach((entry) => {
			const option = document.createElement('option');
			option.value = type === 'pr' ? String(entry.number) : entry.name;
			option.textContent = type === 'pr'
				? `#${entry.number} — ${entry.title}`
				: entry.name;
			select.appendChild(option);
		});
		if (!entries.length) {
			const option = document.createElement('option');
			option.value = '';
			option.textContent = type === 'pr' ? 'No matching open PRs' :
				(type === 'branch' ? 'No branches found' : 'No tags found');
			select.appendChild(option);
		} else if (entries.some((entry) =>
			(type === 'pr' ? String(entry.number) : entry.name) === selected
		)) {
			select.value = selected;
		} else if (type === 'branch' && entries.some((entry) => entry.name === 'main')) {
			select.value = 'main';
		}
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
			setText('developer-status', 'Loading developer options...');
			bridge.loadDeveloperData();
		}
	}

	function install(sourceType, sourceName, label, warning = '') {
		const prompt = updateData && updateData.git_clone
			? `Switch this Git checkout to ${label}?\n\nYour local branches and commits will not be reset. The checkout must be clean, and Anki must be restarted afterward.`
			: `Update Ankimon to ${label}?\n\nYour Pokemon data, settings, and sprites will be preserved.`;
		const message = warning ? `${warning}\n\n${prompt}` : prompt;
		showModal('Confirm Update', message, 'Install', () => {
			setOperationStatus(updateData && updateData.git_clone
				? `Preparing Git checkout for ${label}...`
				: `Downloading ${label}...`);
			bridge.installUpdate(sourceType, sourceName, (result) => {
				if (result && result.success === false) {
					setOperationStatus(result.message || 'Update could not be started.');
				}
			});
		});
	}

	function installActiveBranch() {
		if (!updateData) return;
		const name = updateData.active_branch || 'main';
		if (updateData.git_clone) {
			install('current', 'current', 'current Git branch');
		} else {
			install('branch', name, `latest ${name}`);
		}
	}

	window.onUpdateData = (data) => {
		updateData = data;
		renderVersion(data);
		renderBranch(data);
		renderReleases(data.releases);
		byId('update-channel').value = data.channel || 'stable';
		byId('branch-snooze').checked = Number(data.skip_until) > Date.now() / 1000;
		byId('sprites-snooze').checked = Boolean(data.sprite_snoozed);
		updateButtonStates();
	};

	window.onDeveloperData = (data) => {
		developerData = data;
		developerLoading = false;
		setText('developer-status', '');
		if (updateData && updateData.git_clone) {
			setText(
				'developer-warning',
				'Git workspace mode is active. Sources are fetched from the official Ankimon repository and checked out with Git; local changes must be committed, stashed, or discarded first.',
			);
		}
		renderDeveloperTargets();
	};

	window.onDeveloperLoadFailed = (data) => {
		developerLoading = false;
		setText('developer-status', `Could not load developer options: ${data.message}`);
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
		if (data.busy && data.kind === 'developer') setText('developer-status', 'Loading developer options...');
		if (data.busy && data.kind === 'sprites-check') setText('sprites-status', 'Checking for sprite updates...');
		if (data.busy && data.kind === 'sprites-install') {
			byId('sprites-progress').classList.remove('hidden');
			byId('sprites-progress').value = 0;
		}
		if (!data.busy && data.kind === 'load' && !currentBusy()) setOperationStatus('', false);
		updateButtonStates();
	};

	window.onUpdateInstallStarted = (data) => {
		setOperationStatus(`Downloading ${data.label}...`);
		setOperationProgress(0);
	};

	window.onUpdateProgress = (data) => setOperationProgress(data.percent);
	window.onUpdateStatus = (data) => setOperationStatus(data.message);

	window.onUpdateFinished = (data) => {
		setOperationProgress(data.success ? 100 : 0, Boolean(data.success));
		setOperationStatus(data.status || data.message, true);
		if (data.success) {
			showModal(
				'Update Complete',
				`${data.message}\n\nPlease restart Anki for changes to take effect.`,
				'Close',
				null,
				false,
			);
		} else {
			showModal('Update Failed', data.message || 'The update failed.', 'Close', null, false);
		}
	};

	window.onSpritesChecked = (result) => {
		const status = byId('sprites-status');
		status.classList.remove('success', 'warning', 'error');
		byId('sprites-install-btn').classList.add('hidden');
		spriteUpdateAvailable = result.status === 'update_available';
		if (result.status === 'up_to_date') {
			status.textContent = 'Sprites are already up to date!';
			status.classList.add('success');
			byId('sprites-progress').value = 100;
			byId('sprites-progress').classList.remove('hidden');
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
	};

	window.onSpriteProgress = (data) => {
		byId('sprites-progress').value = data.percent;
	};

	window.onSpriteStatus = (data) => setText('sprites-status', data.message);

	window.onSpritesFinished = (data) => {
		spriteUpdateAvailable = false;
		byId('sprites-install-btn').classList.add('hidden');
		byId('sprites-progress').value = data.success ? 100 : 0;
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

	byId('update-channel').addEventListener('change', (event) => {
		if (bridge) {
			bridge.setChannel(event.target.value, (saved) => {
				if (!saved) showToast('Could not save update channel.', true);
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
	byId('git-pull-btn').addEventListener('click', () => install('current', 'current', 'current Git branch'));
	byId('latest-release-btn').addEventListener('click', () => {
		if (latestRelease) install('release', latestRelease.name, `latest release (${latestRelease.name})`);
	});
	byId('release-select').addEventListener('change', updateButtonStates);
	byId('release-install-btn').addEventListener('click', () => {
		const release = updateData && (updateData.releases || []).find(
			(item) => item.name === byId('release-select').value,
		);
		if (release) install('release', release.name, `release ${release.name}`);
	});

	byId('source-type').addEventListener('change', () => renderDeveloperTargets(true));
	byId('source-target').addEventListener('change', updateButtonStates);
	byId('pr-search').addEventListener('input', () => renderDeveloperTargets(false));
	byId('developer-install-btn').addEventListener('click', () => {
		const source = byId('source-type').value;
		if (source === 'main') {
			install('branch', 'main', 'latest main');
			return;
		}
		const value = byId('source-target').value;
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
		);
	});

	byId('sprites-check-btn').addEventListener('click', () => {
		if (!bridge) return;
		spriteUpdateAvailable = false;
		byId('sprites-install-btn').classList.add('hidden');
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
