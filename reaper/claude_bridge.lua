--[[
  claude_bridge.lua
  Bridge Claude <-> REAPER for the deep Lua tasks the MCP server cannot express.

  This file is the listener. It is loaded by __startup.lua, which REAPER runs
  automatically at launch. Installing it as its own file rather than as
  __startup.lua matters: plenty of people already have a __startup.lua, and
  overwriting it silently destroys their setup. The installer creates or appends
  a single dofile() line instead.

  Protocol - a directory under <REAPER resource path>/claude_bridge:

     cmd.lua     Lua sent by Claude. Consumed and deleted on execution.
     out.txt     Result of the most recent command.
     status.txt  Heartbeat. Refreshed while the listener is alive, so the
                 health check can tell "not running" from "running but slow".
     log.txt     Command history with results.

  To stop it: Actions > "ReaScript: Close all running scripts", or click the
  running-script indicator at the bottom of the REAPER window.
--]]

local SEP  = package.config:sub(1, 1)
local DIR  = reaper.GetResourcePath() .. SEP .. "claude_bridge"
local FIN  = DIR .. SEP .. "cmd.lua"
local FOUT = DIR .. SEP .. "out.txt"
local FLOG = DIR .. SEP .. "log.txt"
local FSTA = DIR .. SEP .. "status.txt"

local POLL_INTERVAL      = 0.15  -- seconds between checks for new work
local HEARTBEAT_INTERVAL = 5.0   -- seconds between status.txt refreshes
local MAX_LOG_BYTES      = 2 * 1024 * 1024

reaper.RecursiveCreateDirectory(DIR, 0)

local function read_file(path)
  local f = io.open(path, "rb")
  if not f then return nil end
  local s = f:read("*a")
  f:close()
  return s
end

local function write_file(path, s, mode)
  local f = io.open(path, mode or "wb")
  if not f then return false end
  f:write(s)
  f:close()
  return true
end

local function file_size(path)
  local f = io.open(path, "rb")
  if not f then return 0 end
  local n = f:seek("end")
  f:close()
  return n or 0
end

-- Turn a Lua return value into text Claude can read.
local function serialize(v)
  if type(v) ~= "table" then return tostring(v) end
  local lines = {}
  for _, val in ipairs(v) do lines[#lines + 1] = tostring(val) end
  if #lines > 0 then return table.concat(lines, "\n") end
  for k, val in pairs(v) do lines[#lines + 1] = tostring(k) .. " = " .. tostring(val) end
  return table.concat(lines, "\n")
end

local function run(code)
  local chunk, err = load(code, "claude_cmd", "t")
  if not chunk then return "PARSE_ERROR: " .. tostring(err) end

  reaper.PreventUIRefresh(1)
  reaper.Undo_BeginBlock()
  local ok, res = pcall(chunk)
  reaper.Undo_EndBlock("Claude bridge command", -1)
  reaper.PreventUIRefresh(-1)
  reaper.TrackList_AdjustWindows(false)
  reaper.UpdateArrange()

  if not ok then return "RUNTIME_ERROR: " .. tostring(res) end
  if res == nil then return "OK" end
  return serialize(res)
end

local last_poll, last_beat = 0, 0

local function heartbeat(state)
  write_file(FSTA, string.format(
    "state=%s\ntime=%s\nreaper=%s\npoll_interval=%.2f\n",
    state, os.date("%Y-%m-%d %H:%M:%S"), reaper.GetAppVersion(), POLL_INTERVAL))
end

local function loop()
  local now = reaper.time_precise()

  if now - last_beat >= HEARTBEAT_INTERVAL then
    last_beat = now
    heartbeat("idle")
  end

  if now - last_poll >= POLL_INTERVAL then
    last_poll = now
    local code = read_file(FIN)
    if code and code:match("%S") then
      os.remove(FIN)
      heartbeat("busy")
      local result = run(code)
      write_file(FOUT, result)

      -- Truncate rather than grow without bound; a long mixing session with
      -- spectrum dumps in it will otherwise reach hundreds of megabytes.
      local mode = "ab"
      if file_size(FLOG) > MAX_LOG_BYTES then mode = "wb" end
      write_file(FLOG,
        ("\n===== %s =====\n--- CMD ---\n%s\n--- RESULT ---\n%s\n")
          :format(os.date("%Y-%m-%d %H:%M:%S"), code, result), mode)

      heartbeat("idle")
    end
  end

  reaper.defer(loop)
end

reaper.atexit(function()
  write_file(FSTA, "state=stopped\ntime=" .. os.date("%Y-%m-%d %H:%M:%S") .. "\n")
end)

heartbeat("starting")
loop()
