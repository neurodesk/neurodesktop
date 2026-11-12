-- Lmod site package, found through LMOD_PACKAGE_PATH=/opt/neurodesktop/lmod.
-- Records each loaded Neurodesk module for neurodesk-cite. Module loading must
-- never fail or wait because of this, so the hook catches every error and only
-- appends one line to a local file.

local hook = require("Hook")

local function neurodesk_roots()
   local local_containers = os.getenv("NEURODESKTOP_LOCAL_CONTAINERS")
   if local_containers == nil or local_containers == "" then
      local_containers = "/neurodesktop-storage/containers"
   end
   return {
      "/cvmfs/neurodesk.ardc.edu.au/",
      local_containers .. "/modules/",
      -- Desktop launchers reach the local store through this symbolic link
      -- when their environment lacks NEURODESKTOP_LOCAL_CONTAINERS.
      "/neurocommand/local/containers/modules/",
   }
end

local function is_neurodesk_module(fn)
   for _, root in ipairs(neurodesk_roots()) do
      if fn:sub(1, #root) == root then
         return true
      end
   end
   return false
end

local function state_dir()
   local state = os.getenv("XDG_STATE_HOME")
   if state == nil or state == "" then
      local home = os.getenv("HOME")
      if home == nil or home == "" then
         return nil
      end
      state = home .. "/.local/state"
   end
   return state .. "/neurodesk"
end

local function make_dirs(path)
   local lfs = require("lfs")
   local prefix = ""
   for part in path:gmatch("[^/]+") do
      prefix = prefix .. "/" .. part
      if lfs.attributes(prefix, "mode") == nil then
         lfs.mkdir(prefix)
      end
   end
end

local function record_load(t)
   local fn = t.fn
   local full_name = t.modFullName
   if type(fn) ~= "string" or type(full_name) ~= "string" or not is_neurodesk_module(fn) then
      return
   end
   local dir = state_dir()
   if dir == nil then
      return
   end
   local name, version = full_name:match("^(.*)/([^/]*)$")
   if name == nil then
      name, version = full_name, ""
   end
   make_dirs(dir)
   local log = io.open(dir .. "/module-usage.tsv", "a")
   if log == nil then
      return
   end
   log:write(name, "\t", version, "\t", os.date("!%Y-%m-%dT%H:%M:%SZ"), "\n")
   log:close()
end

hook.register("load", function(t)
   pcall(record_load, t)
end)
