/**
 * Ankimon Backup Manager
 *
 * This file powers the "Backup Manager" window in Ankimon. It's the piece
 * of JavaScript that lets players save, restore, and manage backups of
 * their Ankimon save data without ever touching the file system themselves.
 *
 * What it does:
 *
 *  - Shows a list of all your existing backups, each one displayed as a
 *    card with the date and time it was made, your trainer name, your
 *    main Pokémon (and its level), how many Pokémon and items you had,
 *    and how much cash you were carrying.
 *
 *  - Lets you create a brand-new backup of your current save with a single
 *    click, after a quick confirmation popup.
 *
 *  - Lets you prepare an old backup for the next full Anki restart. The
 *    current save stays active until then, and the UI keeps restart and
 *    cancellation guidance visible after staging.
 *
 *  - Lets you delete a backup you no longer want, again with a
 *    confirmation popup to prevent accidents.
 *
 *  - Lets you open the folder where backups are stored on your computer,
 *    in case you want to copy them somewhere safe or inspect them
 *    manually.
 *
 *  - Shows friendly toast notifications when something succeeds or fails,
 *    and displays detailed error info (including tracebacks) directly in
 *    the list if the Python side reports a problem.
 *
 * How it talks to the rest of Ankimon:
 *
 *  - It communicates with the Python backend through a QWebChannel bridge
 *    called `backup`. That bridge exposes methods like `getBackups`,
 *    `createBackup`, `restoreBackup`, `deleteBackup`, and `openBackupFolder`.
 *
 *  - There's also a `nav` bridge used to wire up the navigation switcher
 *    and to close the window when the close button is clicked.
 *
 *  - The Python side can call `window.initializeBackupManager(data)` to
 *    push a fresh list of backups into the UI at any time.
 *    Restore completion is pushed through `window.onBackupRestored(data)`.
 *    Create completion is pushed through `window.onBackupCreated(data)`.
 *
 */

(function () {
	'use strict';

	let backupBridge;
	let modalAction = null;
	let restoreResultHandler = null;
	let createResultHandler = null;
	let contextToken = null;
	let actionInProgress = false;
	let bridgeActionBusy = false;
	let listRequest = 0;
	let refreshTimer = null;
	let renderedSnapshot = null;
	let modalOpener = null;
	const pageId = `${Date.now()}-${Math.random()}`;

	window.onBackupsLoaded = (data) => {
		if (!data || data.request_id !== `${pageId}:${listRequest}` || data.pending === true) return;
		if (data.retry || data.stale) {
			clearTimeout(refreshTimer);
			refreshTimer = setTimeout(refresh, 150);
			return;
		}
		render(data);
	};

	window.onBackupRestored = (result) => {
		if (restoreResultHandler) {
			restoreResultHandler(result);
		}
	};

	window.onBackupCreated = (result) => {
		if (createResultHandler) {
			createResultHandler(result);
		}
	};

	function money(value) {
		return `$${value ?? 0}`;
	}

	function render(data) {
		bridgeActionBusy = Boolean(data && data.busy);
		if (data && data.error) {
			renderError(data.error);
			setActionBusy(actionInProgress || bridgeActionBusy);
			return;
		}
		const backups = (data && data.backups) || [];
		const snapshot = JSON.stringify([data.context, backups]);
		if (modalAction && snapshot === renderedSnapshot) {
			renderPendingStatus(data.pending);
			return;
		}
		if (modalAction && snapshot !== renderedSnapshot) closeModal();
		renderedSnapshot = snapshot;
		contextToken = data.context || null;
		renderPendingStatus(data.pending);
		const list = document.getElementById('backup-list');
		list.replaceChildren();
		if (!backups.length) {
			list.innerHTML = '<div class="backup-empty">No backups found.</div>';
			setActionBusy(actionInProgress || bridgeActionBusy);
			return;
		}
		backups.forEach((backup) => {
			const card = document.createElement('article');
			card.className = 'backup-card';

			const actions = document.createElement('div');
			actions.className = 'backup-actions';
			const restoreBtn = document.createElement('button');
			restoreBtn.className = 'restore-btn';
			restoreBtn.textContent = 'Restore Backup';
			const deleteBtn = document.createElement('button');
			deleteBtn.className = 'delete-btn';
			deleteBtn.textContent = 'Delete Backup';
			actions.append(restoreBtn, deleteBtn);

			const dateRow = document.createElement('div');
			dateRow.className = 'backup-date';
			const dateStrong = document.createElement('strong');
			const [date = '', time = ''] = String(backup.date || ' ').split(' ');
			dateStrong.textContent = date;
			const timeSpan = document.createElement('span');
			timeSpan.textContent = `Time: ${time.replaceAll('-', ':')}`;
			dateRow.append(dateStrong, timeSpan);

			const trainerRow = document.createElement('div');
			trainerRow.className = 'backup-trainer';
			const trainerStrong = document.createElement('strong');
			trainerStrong.textContent = backup.trainer_name || 'N/A';
			const mainSpan = document.createElement('span');
			const mainName = backup.main_pokemon_name || 'N/A';
			const mainLevel = backup.main_pokemon_level ?? 'N/A';
			mainSpan.textContent = `${mainName} (Lv. ${mainLevel})`;
			trainerRow.append(trainerStrong, mainSpan);

			const statRow = document.createElement('div');
			statRow.className = 'backup-stat';
			const pokemonSpan = document.createElement('span');
			pokemonSpan.textContent = `${backup.pokemon_count || 0} Pokémon`;
			const itemSpan = document.createElement('span');
			itemSpan.textContent = `${backup.item_count || 0} Items`;
			statRow.append(pokemonSpan, itemSpan);

			const cashRow = document.createElement('div');
			cashRow.className = 'backup-cash';
			cashRow.textContent = money(backup.trainer_cash);

			const details = document.createElement('div');
			details.className = 'backup-details';
			const saveMeta = document.createElement('div');
			saveMeta.className = 'backup-detail-group backup-save-meta';
			saveMeta.append(dateRow, statRow);
			const trainerMeta = document.createElement('div');
			trainerMeta.className = 'backup-detail-group backup-trainer-meta';
			trainerMeta.append(trainerRow, cashRow);
			details.append(saveMeta, trainerMeta);
			card.append(details, actions);

			actions.addEventListener('click', (event) => event.stopPropagation());
			restoreBtn.addEventListener('click', () => {
				const confirmedContext = contextToken;
				if (!confirmedContext) {
					showToast('Backup Manager has not confirmed the active account and profile yet.', true);
					return;
				}
				openModal(
					'Prepare Restore',
					`Prepare the backup from ${backup.date} for ${backup.trainer_name || 'Unknown trainer'}? It will replace the active save on the next full Anki restart. Your current save stays active until then.`,
					() => restoreBackup(backup.path, confirmedContext),
				);
			});
			deleteBtn.addEventListener('click', () => {
				openModal('Delete Backup', `Permanently delete the backup from ${backup.date} for ${backup.trainer_name || 'Unknown trainer'}?`, () => deleteBackup(backup.path));
			});
			list.appendChild(card);
		});
		setActionBusy(actionInProgress || bridgeActionBusy);
	}

	function refresh() {
		if (backupBridge) {
			clearTimeout(refreshTimer);
			const request = `${pageId}:${++listRequest}`;
			backupBridge.getBackups(request, window.onBackupsLoaded);
		} else {
			renderError('Backup Manager is still connecting to Ankimon.');
		}
	}

	function renderPendingStatus(pending) {
		const status = document.getElementById('backup-status');
		status.replaceChildren();
		if (!pending) {
			status.classList.add('hidden');
			return;
		}

		const title = document.createElement('strong');
		const state = pending.state || 'pending';
		title.textContent = {
			pending: 'Save replacement prepared for the next full Anki restart',
			installed: 'Save import already installed',
			unknown: 'Save import status could not be determined',
			cancelled: 'Save import cancelled',
		}[state] || 'Save import status';
		const message = document.createElement('p');
		message.textContent = pending.target
			? `${pending.target}: ${pending.message}`
			: pending.message;
		status.append(title, message);

		let warning = pending.warning || '';
		if (!warning && pending.token) {
			try {
				warning = localStorage.getItem(`ankimon-backup-warning-${pending.token}`) || '';
			} catch (error) {
				console.warn('Could not read the saved restore warning.', error);
			}
		}
		if (warning) {
			const warningText = document.createElement('p');
			warningText.className = 'backup-status-warning';
			warningText.textContent = warning;
			status.appendChild(warningText);
		}
		status.classList.remove('hidden');
	}

	function showPreparedStatus(warning, token, target) {
		const status = document.getElementById('backup-status');
		const pending = {
			state: 'pending',
			target,
			message: 'Your current save remains active until Anki fully exits. Restart Anki manually to install this restore. To cancel it, use Ankimon → Game → Cancel Pending Save Import.',
			warning,
			token,
		};
		if (warning && token) {
			try {
				localStorage.setItem(`ankimon-backup-warning-${token}`, warning);
			} catch (error) {
				console.warn('Could not save the restore warning for later.', error);
			}
		}
		renderPendingStatus(pending);
		status.scrollIntoView({ block: 'nearest' });
	}

	function setActionBusy(busy) {
		document.getElementById('manual-backup-btn').disabled = busy;
		document.getElementById('open-folder-btn').disabled = busy;
		document.querySelectorAll('.backup-actions button').forEach((button) => {
			button.disabled = busy;
		});
	}

	function beginAction() {
		if (actionInProgress || bridgeActionBusy) {
			showToast('Another backup action is already in progress.', true);
			return false;
		}
		actionInProgress = true;
		setActionBusy(true);
		return true;
	}

	function finishAction() {
		actionInProgress = false;
		setActionBusy(bridgeActionBusy);
	}

	function renderError(message) {
		renderFailure(message, null);
	}

	function showToast(message, isError = false) {
		const toast = document.getElementById('toast');
		toast.textContent = message;
		toast.classList.toggle('error', isError);
		toast.classList.add('visible');
		clearTimeout(toast._timer);
		toast._timer = setTimeout(() => toast.classList.remove('visible'), 3000);
	}

	function renderFailure(message, trace) {
		closeModal();
		contextToken = null;
		const empty = document.createElement('div');
		empty.className = 'backup-empty backup-error';
		const title = document.createElement('strong');
		title.textContent = message || 'Backup Manager operation failed.';
		empty.appendChild(title);
		if (trace) {
			const details = document.createElement('pre');
			details.textContent = trace;
			empty.appendChild(details);
		}
		const list = document.getElementById('backup-list');
		list.replaceChildren(empty);
	}

	function createBackup() {
		if (!backupBridge) {
			renderError('Backup Manager is still connecting to Ankimon.');
			return;
		}
		const confirmedContext = contextToken;
		if (!confirmedContext) {
			showToast('Backup Manager has not confirmed the active account and profile yet.', true);
			return;
		}
		openModal('Create New Backup', 'Create and save a backup of your current Ankimon data?', () => {
			let completed = false;
			if (!beginAction()) return;
			const handleResult = (result) => {
				if (!result || result.pending === true || completed) {
					return;
				}
				completed = true;
				createResultHandler = null;
				finishAction();
				if (result.ok === false) {
					console.error(result.traceback || result.error || 'Could not create backup.');
					showToast(result.error || 'Could not create backup.', true);
					refresh();
					return;
				}
				showToast('Manual backup created successfully!');
				refresh();
			};
			createResultHandler = handleResult;
			try {
				backupBridge.createBackup(confirmedContext, handleResult);
			} catch (error) {
				handleResult({ ok: false, error: error.message });
			}
		});
	}

	function deleteBackup(path) {
		if (!backupBridge || !path || !beginAction()) return;
		let completed = false;
		const handleResult = (result) => {
			if (completed || !result || result.pending === true) return;
			completed = true;
			finishAction();
			if (result && result.ok === false) {
				console.error(result.traceback || result.error || 'Could not delete backup.');
				showToast(result.error || 'Could not delete backup.', true);
				refresh();
				return;
			}
			showToast('Backup successfully deleted!');
			refresh();
		};
		try {
			backupBridge.deleteBackup(path, handleResult);
		} catch (error) {
			handleResult({ ok: false, error: error.message });
		}
	}

	function restoreBackup(path, confirmedContext) {
		if (!backupBridge || !path) {
			showToast('Backup restoration failed. Please try again.', true);
			return;
		}
		if (!confirmedContext || !beginAction()) return;
		let completed = false;
		const handleResult = (result) => {
			if (!result || result.pending === true || completed) {
				return;
			}
			completed = true;
			restoreResultHandler = null;
			finishAction();

			if (result.ok === true && result.pending_restart === true) {
				showPreparedStatus(
					result.warning || '', result.pending_token, result.pending_target,
				);
				showToast('Restore prepared. Restart Anki fully to apply it.');
				refresh();
				return;
			}

			if (result.ok !== true) {
				console.error(result.traceback || result.error || 'Backup restoration failed.');
				showToast(
					result.error || 'Backup restoration failed. Please try again.',
					true,
				);
				refresh();
				return;
			}

			showToast('Backup restoration failed. Please try again.', true);
			refresh();
		};
		restoreResultHandler = handleResult;
		try {
			backupBridge.restoreBackup(path, confirmedContext, handleResult);
		} catch (error) {
			handleResult({ ok: false, error: error.message });
		}
	}

	function openModal(title, message, action) {
		if (actionInProgress || bridgeActionBusy || modalAction) return;
		modalOpener = document.activeElement;
		document.querySelector('.app-container').inert = true;
		document.getElementById('backup-modal-title').textContent = title;
		document.getElementById('backup-modal-message').textContent = message;
		document.getElementById('backup-modal-confirm').textContent = title;
		document.getElementById('backup-modal-cancel').classList.remove('hidden');
		document.querySelector('.backup-modal-card').classList.remove('error');
		modalAction = action;
		document.getElementById('backup-modal').classList.remove('hidden');
		document.getElementById('backup-modal-cancel').focus();
	}

	window.showBackupNotice = (message, isError) => {
		closeModal();
		refresh();
		modalOpener = document.activeElement;
		document.querySelector('.app-container').inert = true;
		document.getElementById('backup-modal-title').textContent =
			isError ? 'Save Import Could Not Be Cancelled' : 'Save Import Status';
		document.getElementById('backup-modal-message').textContent = message;
		document.getElementById('backup-modal-cancel').classList.add('hidden');
		document.getElementById('backup-modal-confirm').textContent = 'OK';
		document.querySelector('.backup-modal-card').classList.toggle('error', Boolean(isError));
		modalAction = null;
		document.getElementById('backup-modal').classList.remove('hidden');
		document.getElementById('backup-modal-confirm').focus();
	};

	function closeModal() {
		modalAction = null;
		document.querySelector('.app-container').inert = false;
		document.getElementById('backup-modal').classList.add('hidden');
		document.getElementById('backup-modal-cancel').classList.remove('hidden');
		document.querySelector('.backup-modal-card').classList.remove('error');
		if (modalOpener && modalOpener.isConnected) modalOpener.focus();
		modalOpener = null;
	}

	window.retireBackupConfirmation = () => {
		closeModal();
		contextToken = null;
		++listRequest;
		clearTimeout(refreshTimer);
	};

	window.initializeBackupManager = render;
	window.refreshBackups = refresh;
	document.addEventListener('DOMContentLoaded', () => {
		document.getElementById('manual-backup-btn').addEventListener('click', createBackup);
		document.getElementById('open-folder-btn').addEventListener('click', () => {
			if (!backupBridge) {
				renderError('Backup Manager is still connecting to Ankimon.');
				return;
			}
			if (beginAction()) {
				let completed = false;
				const handleResult = (result) => {
					if (completed) return;
					completed = true;
					finishAction();
					if (!result || result.ok === false) {
						console.error(result && (result.traceback || result.error));
						showToast((result && result.error) || 'Could not open the backup folder.', true);
					}
				};
				try {
					backupBridge.openBackupFolder(handleResult);
				} catch (error) {
					handleResult({ ok: false, error: error.message });
				}
			}
		});
		document.getElementById('backup-modal-cancel').addEventListener('click', closeModal);
		document.getElementById('backup-modal-confirm').addEventListener('click', () => {
			const action = modalAction;
			closeModal();
			if (action) action();
		});
		document.getElementById('backup-modal').addEventListener('click', (event) => {
			if (event.target.id === 'backup-modal') closeModal();
		});
		document.addEventListener('keydown', (event) => {
			const modal = document.getElementById('backup-modal');
			if (modal.classList.contains('hidden')) return;
			if (event.key === 'Escape') {
				event.preventDefault();
				closeModal();
			} else if (event.key === 'Tab') {
				const buttons = [...modal.querySelectorAll('button')].filter((button) => !button.classList.contains('hidden'));
				const index = buttons.indexOf(document.activeElement);
				const next = (index + (event.shiftKey ? -1 : 1) + buttons.length) % buttons.length;
				event.preventDefault();
				buttons[next].focus();
			}
		});
		document.getElementById('close-btn').addEventListener('click', () => {
			if (window.navBridge) window.navBridge.closeWindow();
		});
		new QWebChannel(qt.webChannelTransport, (channel) => {
			backupBridge = channel.objects.backup;
			window.navBridge = channel.objects.nav;
			window.wireNavSwitcher(window.navBridge);
			refresh();
		});
	});
	window.addEventListener('pagehide', window.retireBackupConfirmation);
	window.addEventListener('focus', refresh);
	window.addEventListener('pageshow', refresh);
	document.addEventListener('visibilitychange', () => {
		if (document.hidden) window.retireBackupConfirmation();
		else refresh();
	});
})();
