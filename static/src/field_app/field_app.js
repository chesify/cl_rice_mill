/* Rice Mill field app: works offline, keeps captured records in IndexedDB and sends them
 * to the server when the connection returns. Plain JavaScript on purpose: the page must
 * stay small for cheap phones on slow connections. */
(function () {
    "use strict";

    const DB_NAME = "cl_rice_field";
    const SYNC_INTERVAL = 60 * 1000;
    let db = null;
    let syncing = false;

    // ------------------------------------------------------------------
    // IndexedDB: "outbox" (records to send, by uuid) and "cache" (data for offline use)
    // ------------------------------------------------------------------
    function openDb() {
        return new Promise((resolve, reject) => {
            const request = indexedDB.open(DB_NAME, 1);
            request.onupgradeneeded = () => {
                request.result.createObjectStore("outbox", { keyPath: "uuid" });
                request.result.createObjectStore("cache");
            };
            request.onsuccess = () => resolve(request.result);
            request.onerror = () => reject(request.error);
        });
    }

    function store(name, mode) {
        return db.transaction(name, mode).objectStore(name);
    }

    function asPromise(request) {
        return new Promise((resolve, reject) => {
            request.onsuccess = () => resolve(request.result);
            request.onerror = () => reject(request.error);
        });
    }

    function outboxAll() {
        return asPromise(store("outbox", "readonly").getAll());
    }

    function outboxPut(record) {
        return asPromise(store("outbox", "readwrite").put(record));
    }

    function cacheGet(key) {
        return asPromise(store("cache", "readonly").get(key));
    }

    function cachePut(key, value) {
        return asPromise(store("cache", "readwrite").put(value, key));
    }

    // ------------------------------------------------------------------
    // Server calls (JSON-RPC)
    // ------------------------------------------------------------------
    async function rpc(path, params) {
        const response = await fetch(path, {
            method: "POST",
            credentials: "same-origin",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ jsonrpc: "2.0", method: "call", params: params || {} }),
        });
        const payload = await response.json();
        if (payload.error) {
            const data = payload.error.data || {};
            const error = new Error(data.message || payload.error.message);
            error.sessionExpired = data.name === "odoo.http.SessionExpiredException";
            throw error;
        }
        return payload.result;
    }

    function newUuid() {
        if (window.crypto && crypto.randomUUID) {
            return crypto.randomUUID();
        }
        const bytes = crypto.getRandomValues(new Uint8Array(16));
        return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
    }

    // ------------------------------------------------------------------
    // Screen
    // ------------------------------------------------------------------
    function message(text) {
        document.getElementById("o_cl_rice_field_message").textContent = text || "";
    }

    function showNetwork() {
        const badge = document.getElementById("o_cl_rice_field_net");
        badge.textContent = navigator.onLine ? "Online" : "Offline";
        badge.className = "o_cl_rice_field_badge " + (navigator.onLine ? "o_cl_rice_field_online" : "o_cl_rice_field_offline");
    }

    function fillSelect(select, items, keepFirst) {
        const first = keepFirst ? select.options[0] : null;
        select.textContent = "";
        if (first) {
            select.appendChild(first);
        }
        for (const item of items) {
            const option = document.createElement("option");
            option.value = item.id;
            option.textContent = item.code ? `${item.code} ${item.name} (${item.village})` : item.name;
            select.appendChild(option);
        }
    }

    function applyData(data) {
        if (!data) {
            message("Connect once to download your farmers.");
            return;
        }
        const form = document.getElementById("o_cl_rice_field_service");
        fillSelect(form.elements.farmer_season_id, data.farmer_seasons, false);
        fillSelect(form.elements.service_type, data.services, false);
        fillSelect(form.elements.vehicle_id, data.machines, true);
        fillSelect(form.elements.operator_id, data.operators, true);
    }

    async function renderOutbox() {
        const records = (await outboxAll()).sort((a, b) => b.created - a.created);
        const list = document.getElementById("o_cl_rice_field_outbox");
        list.textContent = "";
        let pending = 0;
        for (const record of records) {
            if (record.status === "pending") {
                pending++;
            }
            const item = document.createElement("li");
            item.className = "o_cl_rice_field_" + record.status;
            const status = { pending: "Waiting to send", synced: "Sent", failed: "Failed" }[record.status];
            item.textContent = `${record.label} - ${status}${record.error ? ": " + record.error : ""}`;
            if (record.status === "failed") {
                const retry = document.createElement("button");
                retry.type = "button";
                retry.textContent = "Retry";
                retry.onclick = async () => {
                    record.status = "pending";
                    record.error = "";
                    await outboxPut(record);
                    await renderOutbox();
                    sync();
                };
                item.appendChild(retry);
            }
            list.appendChild(item);
        }
        document.getElementById("o_cl_rice_field_count").textContent = pending;
    }

    // ------------------------------------------------------------------
    // Capture
    // ------------------------------------------------------------------
    function resizePhoto(file) {
        // keep photos small: 800 px wide JPEG
        return new Promise((resolve) => {
            if (!file) {
                resolve(null);
                return;
            }
            const image = new Image();
            image.onload = () => {
                const scale = Math.min(1, 800 / image.width);
                const canvas = document.createElement("canvas");
                canvas.width = image.width * scale;
                canvas.height = image.height * scale;
                canvas.getContext("2d").drawImage(image, 0, 0, canvas.width, canvas.height);
                resolve(canvas.toDataURL("image/jpeg", 0.6));
            };
            image.onerror = () => resolve(null);
            image.src = URL.createObjectURL(file);
        });
    }

    async function saveRecord(kind, data, label) {
        await outboxPut({ uuid: newUuid(), kind, data, label, status: "pending", error: "", created: Date.now() });
        await renderOutbox();
        message("Saved. It will be sent when the connection is available.");
        sync();
    }

    async function onServiceSubmit(event) {
        event.preventDefault();
        const form = event.target;
        const values = Object.fromEntries(new FormData(form).entries());
        values.photo = await resizePhoto(form.elements.photo.files[0]);
        const farmer = form.elements.farmer_season_id.selectedOptions[0];
        const service = form.elements.service_type.selectedOptions[0];
        await saveRecord("service_job", values, `${service ? service.textContent : ""} - ${farmer ? farmer.textContent : ""}`);
        form.reset();
        form.elements.date.valueAsDate = new Date();
    }

    async function onFarmerSubmit(event) {
        event.preventDefault();
        const form = event.target;
        const values = Object.fromEntries(new FormData(form).entries());
        await saveRecord("farmer", values, `New farmer ${values.name}`);
        form.reset();
        document.getElementById("o_cl_rice_field_gps_status").textContent = "";
    }

    function onGps() {
        const status = document.getElementById("o_cl_rice_field_gps_status");
        if (!navigator.geolocation) {
            status.textContent = "Location is not available on this phone.";
            return;
        }
        status.textContent = "Finding location...";
        navigator.geolocation.getCurrentPosition(
            (position) => {
                const form = document.getElementById("o_cl_rice_field_farmer");
                form.elements.latitude.value = position.coords.latitude.toFixed(7);
                form.elements.longitude.value = position.coords.longitude.toFixed(7);
                status.textContent = `Location: ${form.elements.latitude.value}, ${form.elements.longitude.value}`;
            },
            () => {
                status.textContent = "Could not get the location.";
            },
            { enableHighAccuracy: true, timeout: 20000 }
        );
    }

    // ------------------------------------------------------------------
    // Synchronisation
    // ------------------------------------------------------------------
    async function refreshData() {
        if (!navigator.onLine) {
            return;
        }
        try {
            const data = await rpc("/rice/field/data");
            await cachePut("data", data);
            applyData(data);
        } catch (error) {
            if (error.sessionExpired) {
                message("Please log in again while online.");
            }
        }
    }

    async function sync() {
        if (syncing || !navigator.onLine) {
            return;
        }
        const pending = (await outboxAll()).filter((record) => record.status === "pending");
        if (!pending.length) {
            return;
        }
        syncing = true;
        try {
            const results = await rpc("/rice/field/sync", {
                records: pending.map(({ uuid, kind, data }) => ({ uuid, kind, data })),
            });
            const byUuid = Object.fromEntries(results.map((result) => [result.uuid, result]));
            for (const record of pending) {
                const result = byUuid[record.uuid];
                if (!result) {
                    continue;
                }
                record.status = result.status === "failed" ? "failed" : "synced";
                record.error = result.error || "";
                if (record.status === "synced") {
                    record.data = { photo: null };  // free the space taken by photos
                }
                await outboxPut(record);
            }
            message("");
        } catch (error) {
            message(error.sessionExpired ? "Please log in again while online." : "Could not send now, will retry.");
        } finally {
            syncing = false;
            await renderOutbox();
        }
    }

    // ------------------------------------------------------------------
    // Start
    // ------------------------------------------------------------------
    function switchTab(name) {
        for (const button of document.querySelectorAll(".o_cl_rice_field_tabs button")) {
            button.classList.toggle("active", button.dataset.tab === name);
        }
        for (const panel of document.querySelectorAll(".o_cl_rice_field_panel")) {
            panel.hidden = panel.dataset.panel !== name;
        }
    }

    async function start() {
        db = await openDb();
        showNetwork();
        for (const button of document.querySelectorAll(".o_cl_rice_field_tabs button")) {
            button.onclick = () => switchTab(button.dataset.tab);
        }
        const serviceForm = document.getElementById("o_cl_rice_field_service");
        serviceForm.elements.date.valueAsDate = new Date();
        serviceForm.onsubmit = onServiceSubmit;
        document.getElementById("o_cl_rice_field_farmer").onsubmit = onFarmerSubmit;
        document.getElementById("o_cl_rice_field_gps").onclick = onGps;
        document.getElementById("o_cl_rice_field_sync").onclick = sync;
        window.addEventListener("online", () => {
            showNetwork();
            refreshData();
            sync();
        });
        window.addEventListener("offline", showNetwork);
        applyData(await cacheGet("data"));
        await renderOutbox();
        await refreshData();
        sync();
        setInterval(sync, SYNC_INTERVAL);
        if ("serviceWorker" in navigator) {
            navigator.serviceWorker.register("/rice/field/sw.js", { scope: "/rice/field/" });
        }
    }

    start();
})();
