--[[
  claude_bridge.lua
  Facilitates execution of Lua scripts in REAPER.

  This file operates as a background process. It is separated from __startup.lua
  to prevent overriding user-defined startup routines.
  
  Files utilized in <REAPER resource path>/claude_bridge:
     cmd.lua     Input script.
     out.txt     Execution output.
     status.txt  Process state indicator.
     log.txt     Execution log.
--]]

local SEP  = package.config:sub(1, 1)
local DIR  = reaper.GetResourcePath() .. SEP .. "claude_bridge"
local FIN  = DIR .. SEP .. "cmd.lua"
local FOUT = DIR .. SEP .. "out.txt"
local FLOG = DIR .. SEP .. "log.txt"
local FSTA = DIR .. SEP .. "status.txt"

local POLL_INTERVAL      = 0.15  -- Dictates script responsiveness to incoming files.
local HEARTBEAT_INTERVAL = 5.0   -- Provides liveliness signal for external monitoring.
local MAX_LOG_BYTES      = 2 * 1024 * 1024

-- Running the startup action again would otherwise leave two listeners polling the
-- same command file: whichever read it first would consume it, both would write
-- results, and a caller could read the wrong one. Each instance claims the newest
-- generation number, and any older instance retires the next time it wakes up.
CLAUDE_BRIDGE_GENERATION = (CLAUDE_BRIDGE_GENERATION or 0) + 1
local MY_GENERATION = CLAUDE_BRIDGE_GENERATION

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

-- Renders a nested value on one line, so a table inside a table is readable
-- instead of appearing as "table: 0x...". Depth and cycles are bounded.
local function compact(v, depth, seen)
  if type(v) ~= "table" then return tostring(v) end
  if depth > 3 then return "{...}" end
  if seen[v] then return "<cycle>" end
  seen[v] = true

  local parts, n = {}, 0
  for _, val in ipairs(v) do
    n = n + 1
    parts[#parts + 1] = compact(val, depth + 1, seen)
  end
  local keys = {}
  for k in pairs(v) do
    if not (type(k) == "number" and k % 1 == 0 and k >= 1 and k <= n) then
      keys[#keys + 1] = k
    end
  end
  table.sort(keys, function(a, b) return tostring(a) < tostring(b) end)
  for _, k in ipairs(keys) do
    parts[#parts + 1] = tostring(k) .. " = " .. compact(v[k], depth + 1, seen)
  end

  seen[v] = nil
  return "{" .. table.concat(parts, ", ") .. "}"
end

-- Text format is required for external process consumption.
-- One line per array element, then any remaining keys. Keys outside the array
-- part used to be discarded whenever the table also held array entries, so a
-- returned table could silently lose half its contents.
local function serialize(v)
  if type(v) ~= "table" then return tostring(v) end
  local seen = {}
  local lines, n = {}, 0

  for _, val in ipairs(v) do
    n = n + 1
    lines[#lines + 1] = compact(val, 1, seen)
  end

  local keys = {}
  for k in pairs(v) do
    if not (type(k) == "number" and k % 1 == 0 and k >= 1 and k <= n) then
      keys[#keys + 1] = k
    end
  end
  -- Sorted so repeated calls return the same order; pairs() order is arbitrary.
  table.sort(keys, function(a, b) return tostring(a) < tostring(b) end)
  for _, k in ipairs(keys) do
    lines[#lines + 1] = tostring(k) .. " = " .. compact(v[k], 1, seen)
  end

  if #lines == 0 then return "{}" end
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
  -- A newer instance has taken over; stop rescheduling and let this one end.
  if MY_GENERATION ~= CLAUDE_BRIDGE_GENERATION then return end

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

      -- A client may append its own request id. Echoing it back lets that client
      -- recognise its own result. Without it, a second client arriving while the
      -- first command is still running reads the first command's output and both
      -- callers end up with the same answer.
      local id = code:match("@claude%-bridge%-id:(%w+)")

      heartbeat("busy")
      local result = run(code)
      if id then
        write_file(FOUT, "@id:" .. id .. "\n" .. result)
      else
        write_file(FOUT, result)
      end

      -- Truncation prevents excessive disk space consumption during prolonged usage.
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
