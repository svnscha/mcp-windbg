"use strict";
/* -------------------------------------------------------- *
 *  WinDbg MCP Plugin – file‑based IPC (TTD‑friendly)
 *  DEBUG‑ENHANCED VERSION (2025‑07‑30)
 * -------------------------------------------------------- */

/* ---------- tiny logger ---------- */
function dbg(msg) {
    const ts = new Date().toISOString();
    host.diagnostics.debugLog(`[MCP][${ts}] ${msg}\n`);
}

/* ---------- robust JSON helpers ---------- */
function writeJson(path, obj) {
    const fs = host.namespace.Debugger.Utility.FileSystem;
    dbg(`writeJson → ${path}`);
    let file;
    try {
        file = fs.CreateFile(path, "CreateAlways");
        const writer = fs.CreateTextWriter(file, "Utf8");
        writer.WriteLine(JSON.stringify(obj, null, 2));
        dbg("writeJson ✔ done");
    } catch (e) {
        dbg(`writeJson ✖ ERROR: ${e.message}`);
        throw e;
    } finally {
        if (file) file.Close();
    }
}

function readFileText(reader) {
    // Attempt ReadToEnd – fastest and safest when available
    if (typeof reader.ReadToEnd === "function") {
        try {
            return reader.ReadToEnd();
        } catch (_) {
            // fall through to manual reading
        }
    }
    // Manual line‑by‑line fallback – tolerate EOF exceptions
    if (typeof reader.ReadLine === "function") {
        let buf = "";
        while (true) {
            try {
                const ln = reader.ReadLine();
                if (ln === undefined || ln === null) break;
                buf += ln;
            } catch (e) {
                // WinDbg throws "Cannot read past end of file" when EOF reached – treat as normal termination
                if (e.message.includes("end of file")) break;
                dbg(`readFileText ReadLine unexpected error: ${e.message}`);
                break;
            }
        }
        return buf;
    }
    // Iterator fallback (very old builds)
    let buf = "";
    try {
        for (const ln of reader) buf += ln;
    } catch (_) { /* swallow */ }
    return buf;
}


function readJson(path) {
    const fs = host.namespace.Debugger.Utility.FileSystem;

    if (!fs.FileExists(path)) {
        dbg("readJson → file not found (return null)");
        return null;
    }

    let file;
    try {
        file = fs.CreateFile(path, "OpenExisting");
        const reader = fs.CreateTextReader(file, "Utf8");
        const txt = readFileText(reader).trim();
        if (!txt) {
            dbg("readJson -> empty file (return null)");
            return null;
        }
        try {
            const obj = JSON.parse(txt);
            return obj;
        } catch (e) {
            dbg(`readJson -> JSON parse error: ${e.message}`);
            return null;
        }
    } catch (e) {
        dbg(`readJson [X] ERROR: ${e.message}`);
        return null;
    } finally {
        if (file) file.Close();
    }
}

/* ---------- global state ---------- */
let g = {
    isInit: false,
    sessionId: null,
    commFile: null,
    resultFile: null,
    shutdownFile: null,
    lastProcessedId: null,
    monitorRunning: false
};

/* ---------- core ops ---------- */
function generateSessionId() {
    return `${Date.now()}_${Math.floor(Math.random() * 1e4)}`;
}

function executeDbg(cmd, id) {
    const ctl = host.namespace.Debugger.Utility.Control;
    dbg(`executeDbg: ${cmd}`);
    const t0 = Date.now();
    let out = [];
    let ok = true,
        err = null;

    try {
        for (const line of ctl.ExecuteCommand(cmd)) out.push(line.toString());
    } catch (e) {
        ok = false;
        err = e.message;
        out.push(`ERROR: ${e.message}`);
        dbg(`executeDbg ✖ command failed: ${e.message}`);
    }

    const res = {
        type: "result",
        id: id ?? `cmd_${Date.now()}`,
        command: cmd,
        success: ok,
        output: out,
        error: err,
        execMs: Date.now() - t0,
        timestamp: Date.now(),
        sessionId: g.sessionId
    };

    writeJson(g.resultFile, res);
    dbg(`executeDbg ✔ wrote result (${res.execMs} ms)`);
    return res;
}

/* ---------- command monitor ---------- */
function pollOnce() {
    const msg = readJson(g.commFile);
    if (!msg) return;
    if (msg.type === "command" && msg.id !== g.lastProcessedId) {
        g.lastProcessedId = msg.id;
        dbg(`pollOnce: got message id=${msg.id}, type=${msg.type}`);
        executeDbg(msg.command, msg.id);
    }
}

function monitorLoop() {
    if (g.monitorRunning) return;
    g.monitorRunning = true;
    const ctl = host.namespace.Debugger.Utility.Control;
    dbg(`monitorLoop started`);

    let cycles = 0;
    while (g.monitorRunning) {
        /* external shutdown marker from Python? */
        if (g.shutdownFile &&
            host.namespace.Debugger.Utility.FileSystem.FileExists(g.shutdownFile)) {
            dbg("shutdown file detected – stopping");
            mcp_shutdown();
            break;
        }
        try {
            pollOnce();
        } catch (e) {
            dbg(`monitorLoop ✖ exception: ${e.message}`);
        }
        cycles++;
        ctl.ExecuteCommand(".sleep 250");
    }

    g.monitorRunning = false;
    dbg("monitorLoop exited.");
}

/* ---------- public commands ---------- */
function mcp_init() {
    dbg("mcp_init invoked");
    if (g.isInit) return "Already initialised.";

    g.sessionId = generateSessionId();
    const tmp = "C:\\Temp";
    g.commFile = `${tmp}\\windbg_mcp_${g.sessionId}.json`;
    g.resultFile = `${tmp}\\windbg_mcp_${g.sessionId}_result.json`;
    g.shutdownFile = `${tmp}\\windbg_mcp_${g.sessionId}_shutdown.json`;

    writeJson(g.commFile, {
        type: "init",
        sessionId: g.sessionId,
        status: "ready",
        timestamp: Date.now()
    });
    g.isInit = true;

    monitorLoop();
    return `MCP ready (session ${g.sessionId})`;
}

function mcp_status() {
    dbg("mcp_status queried");
    return {
        sessionId: g.sessionId,
        commFile: g.commFile,
        resultFile: g.resultFile,
        running: g.monitorRunning
    };
}

function mcp_shutdown() {
    dbg("mcp_shutdown invoked");
    if (!g.isInit) return "Not initialised.";

    writeJson(g.shutdownFile, {
        type: "shutdown",
        sessionId: g.sessionId,
        timestamp: Date.now()
    });
    g.monitorRunning = false;
    g.isInit = false;
    dbg("mcp_shutdown ✔ complete");
    return "MCP shut down.";
}

function mcp_exec(cmd) {
    dbg(`mcp_exec: ${cmd}`);
    if (!g.isInit) return "Init first.";
    return executeDbg(cmd);
}

function mcp_check() {
    dbg("mcp_check manual poke");
    return pollOnce() ?? "No new command.";
}

/* ---------- script registration ---------- */
function initializeScript() {
    dbg("initializeScript – registering aliases");
    return [
        new host.functionAlias(mcp_init, "mcp_init"),
        new host.functionAlias(mcp_status, "mcp_status"),
        new host.functionAlias(mcp_shutdown, "mcp_shutdown"),
        new host.functionAlias(mcp_exec, "mcp_exec"),
        new host.functionAlias(mcp_check, "mcp_check")
    ];
}

function invokeScript() {
    dbg("WinDbg MCP plugin loaded.  !mcp_init to start.");
    return initializeScript();
}

invokeScript();
