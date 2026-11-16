# Working on the TV

Reach the rooted TV with the webOS CLI from `node_modules/.bin` (installed by `nub install`). The
README's `--device webos` is a placeholder; take the real device name from
`ares-setup-device --list`. Use `ares-novacom --run` for root commands: `ares-shell` is unsupported
for the `tv` profile, and plain `ssh root@<tv>` lacks the key ares uses.

These workarounds were needed on the LG C3 (webOS 25):

- **Installing an IPK**: `ares-install` uploads the package to `/media/developer/temp/`, then fails
  with `Unable to exec`. Finish with
  `luna-send -n 30 -w 180000 luna://com.webos.appInstallService/dev/install '{"id":"<app id>","ipkUrl":"<uploaded path>","subscribe":true}'`
  run from a script on the TV, and wait for `"state":"installed"`. The
  `install has been disconnected` error after it is the subscription closing.
- **Remote jobs**: write each job that calls `luna-send` into a script on the TV and run it with
  `sh`, because `luna-send` prints nothing when called directly in a `--run` command. Send the
  script as base64:
  `ares-novacom -d <device> --run "echo <base64> | base64 -d > /tmp/job.sh; sh /tmp/job.sh; rm -f /tmp/job.sh"`.
  For a single call, `script -qc "luna-send ..." /dev/null` also works.
- **Large payloads**: a 23 KB `--run` command failed with `Unable to exec`, while 8 KB commands
  worked. Append larger payloads to a file in 8 KB base64 chunks, then compare the reassembled
  file's SHA-256.
- **Process checks**: match with a bracket pattern such as `pgrep -f '[m]anaged_mapper.py'`, so the
  shell running the `--run` command does not match itself.
- **Boot investigations**: `/var/log` lives in RAM, so `/var/log/messages` and `bootd.log` cover
  only the current boot. Read them before the next reboot. Hook timings come from `/proc/<pid>/stat`
  start times (field 22, in 1/100 s).
