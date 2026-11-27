#!/usr/bin/env python3
"""Production-method CEC power interpretation tests with native transport adapters.

Uses actual CEC types, P8 mutexes, key/release/timeout methods and LG init bodies.
The bus, callback delivery and clock are deterministic host adapters. This is not
a full libCEC link, target execution, HDMI simulation or physical acceptance.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess


def block(text, marker, semicolon=False):
    start = text.index(marker)
    opening = text.index('{', start)
    level, end = 1, opening + 1
    while level:
        level += (text[end] == '{') - (text[end] == '}')
        end += 1
    return text[start:end] + (';' if semicolon else '')


PREAMBLE = r'''
#include <algorithm>
#include <cstdlib>
#include <iostream>
#include <memory>
#include <string>
#include <vector>
#include "cectypes.h"
#include "p8-platform/threads/mutex.h"
using namespace CEC;
using namespace P8PLATFORM;
int64_t now = 1000;
int64_t suspendedMs = 0;
bool bootClockWorks = true;
int64_t FixtureTimeMs() { return now; }
#define GetTimeMs FixtureTimeMs
int FixtureClockGetTime(clockid_t id, struct timespec* time) {
  if (id != CLOCK_BOOTTIME || !bootClockWorks) return -1;
  time->tv_sec=(now+suspendedMs)/1000;
  time->tv_nsec=((now+suspendedMs)%1000)*1000000;
  return 0;
}
#define clock_gettime FixtureClockGetTime
const unsigned CEC_PROCESSOR_SIGNAL_WAIT_TIME = 1000;
struct Library {
  template<class... Args> void AddLog(Args...) {}
};
Library library;
#define LIB_CEC (&library)
#define ToString(x) "key"
#define COMMAND_HANDLED 0xff
unsigned checks = 0;
void require(bool value, const char* message) {
  ++checks;
  if (!value) { std::cerr << "FAIL: " << message << '\n'; std::exit(1); }
}
class CCECProcessor;
class CCECClient {
public:
  @STATE@
  CMutex m_mutex, m_cbMutex;
  libcec_configuration m_configuration;
  bool m_bInitialised = true, m_bRegistered = true;
  cec_user_control_code m_iCurrentButton = CEC_USER_CONTROL_CODE_UNKNOWN;
  int64_t m_initialButtontime=0, m_updateButtontime=0, m_repeatButtontime=0;
  int64_t m_releaseButtontime=0;
  int m_pressedButtoncount=0, m_releasedButtoncount=0;
  std::vector<cec_keypress> keys;
  CCECClient() { m_configuration.Clear(); }
  void AddKey(bool bSendComboKey=false, bool bButtonRelease=false);
  void AddKey(const cec_keypress& key);
  void SetCurrentButton(cec_user_control_code);
  uint16_t CheckKeypressTimeout();
  void SetInitialised(bool);
  void SetRegistered(bool);
  @POWER_DECL@
  void QueueAddKey(const cec_keypress& key) { keys.push_back(key); }
  uint32_t DoubleTapTimeoutMS() { return m_configuration.iDoubleTapTimeoutMs; }
};
typedef std::shared_ptr<CCECClient> CECClientPtr;
class CCECBusDevice {
public:
  cec_logical_address address;
  cec_power_status power = CEC_POWER_STATUS_ON;
  bool active = true, handled = true;
  unsigned activations=0, inactive=0, standbyReplies=0;
  CECClientPtr client;
  CCECBusDevice(cec_logical_address a, CECClientPtr c) : address(a), client(c) {}
  CECClientPtr GetClient() { return client; }
  bool IsHandledByLibCEC() { return handled; }
  bool IsActiveSource() { return active; }
  cec_power_status GetCurrentPowerStatus() { return power; }
  void SetPowerStatus(cec_power_status value) { power=value; }
  void ActivateSource() { ++activations; active=true; power=CEC_POWER_STATUS_ON; }
  void MarkAsActiveSource() { active=true; power=CEC_POWER_STATUS_ON; }
  void MarkAsInactiveSource() { ++inactive; active=false; }
  void TransmitInactiveSource() {}
  void SetMenuState(cec_menu_state) {}
  void TransmitPowerState(cec_logical_address, bool) { ++standbyReplies; }
};
class CCECProcessor {
public:
  bool initialised = true;
  CCECBusDevice* local;
  CCECBusDevice* other = nullptr;
  explicit CCECProcessor(CCECBusDevice* d) : local(d) {}
  bool CECInitialised() { return initialised; }
  CCECBusDevice* GetDevice(cec_logical_address a) {
    if (local && local->address==a) return local;
    if (other && other->address==a) return other;
    return nullptr;
  }
  bool IsHandledByLibCEC(cec_logical_address a) {
    auto d=GetDevice(a); return d && d->handled;
  }
  CECClientPtr GetClient(cec_logical_address a) {
    auto d=GetDevice(a); return d ? d->client : CECClientPtr();
  }
  CCECBusDevice* GetPrimaryDevice() { return local; }
};
class CCECCommandHandler {
public:
  CCECProcessor* m_processor;
  explicit CCECCommandHandler(CCECProcessor* p) : m_processor(p) {}
  CCECBusDevice* GetDevice(cec_logical_address a) { return m_processor->GetDevice(a); }
  int HandleUserControlPressed(const cec_command&);
  int HandleUserControlRelease(const cec_command&);
  int HandleDeviceVendorCommandWithId(const cec_command&) { return CEC_ABORT_REASON_INVALID_OPERAND; }
};
class CSLCommandHandler : public CCECCommandHandler {
public:
  CMutex m_SLMutex;
  bool m_bSLEnabled=false;
  using CCECCommandHandler::CCECCommandHandler;
  void HandleVendorCommandSLInit(const cec_command&);
  void HandleVendorCommandPowerOnStatus(const cec_command&);
  void TransmitVendorCommandSLAckInit(cec_logical_address, cec_logical_address);
  void SetSLInitialised();
  bool Transmit(cec_command&, bool, bool) { return true; }
};
class CRLCommandHandler : public CCECCommandHandler {
public:
  using CCECCommandHandler::CCECCommandHandler;
  int HandleDeviceVendorCommandWithId(const cec_command&);
};
#define RL_KEY_TOP_MENU 0x10
#define RL_KEY_DVD_MENU 0x11
#define SL_COMMAND_ACK_INIT 0x02
#define SL_COMMAND_TYPE_HDDRECORDER 0x05
'''


TESTS = r'''
struct Fixture {
  CECClientPtr client = std::make_shared<CCECClient>();
  CCECBusDevice device{CECDEVICE_PLAYBACKDEVICE2, client};
  CCECProcessor processor{&device};
  CSLCommandHandler handler{&processor};
  cec_command command(cec_user_control_code key, cec_logical_address from=CECDEVICE_TV,
                      cec_logical_address to=CECDEVICE_PLAYBACKDEVICE2) {
    cec_command c;
    cec_command::Format(c,from,to,CEC_OPCODE_USER_CONTROL_PRESSED);
    c.PushBack(key); return c;
  }
  void press(cec_user_control_code key=CEC_USER_CONTROL_CODE_POWER,
             cec_logical_address from=CECDEVICE_TV,
             cec_logical_address to=CECDEVICE_PLAYBACKDEVICE2) {
    require(handler.HandleUserControlPressed(command(key,from,to))==COMMAND_HANDLED,
            "valid press handled");
  }
  void release(cec_logical_address from=CECDEVICE_TV,
               cec_logical_address to=CECDEVICE_PLAYBACKDEVICE2) {
    cec_command c;
    cec_command::Format(c,from,to,CEC_OPCODE_USER_CONTROL_RELEASE);
    require(handler.HandleUserControlRelease(c)==COMMAND_HANDLED,"release handled");
  }
  unsigned shutdownKeys() {
    unsigned n=0;
    for (const auto& key:client->keys)
      n += key.keycode==CEC_USER_CONTROL_CODE_POWER ||
           key.keycode==CEC_USER_CONTROL_CODE_POWER_TOGGLE_FUNCTION ||
           key.keycode==CEC_USER_CONTROL_CODE_POWER_OFF_FUNCTION;
    return n;
  }
  void wake() { device.active=false; device.power=CEC_POWER_STATUS_STANDBY; press(); }
};

void test(const std::string& name) {
  Fixture f;
  if (name=="lg-replay" || name=="lg-no-shutdown") {
    f.device.active=false;
    auto c=f.command(CEC_USER_CONTROL_CODE_POWER);
    f.handler.HandleVendorCommandPowerOnStatus(c);
    require(f.device.power==CEC_POWER_STATUS_ON,"LG reconnect caches on");
    f.handler.HandleVendorCommandSLInit(c);
    require(f.device.power==CEC_POWER_STATUS_STANDBY,"LG init caches standby");
    f.press();
    require(f.device.active && f.device.activations==1,"native LG wake preserved");
    require(f.shutdownKeys()==0,"wake must not reach application as shutdown key");
    if(name=="lg-replay") require(f.client->keys.size()==1 &&
            f.client->keys[0].keycode==CEC_USER_CONTROL_CODE_POWER_ON_FUNCTION &&
            f.client->keys[0].duration==0,"wake callback is an activation press");
    now+=100; f.release();
    require(f.shutdownKeys()==0,"wake release must not become shutdown");
    if(name=="lg-replay") require(f.client->keys.size()==2 &&
            f.client->keys[1].keycode==CEC_USER_CONTROL_CODE_POWER_ON_FUNCTION &&
            f.client->keys[1].duration==100,"wake callback has its exact activation release");
  } else if (name=="held-wake" || name=="held-wake-repeat") {
    f.client->m_configuration.iButtonRepeatRateMs=name=="held-wake-repeat"?100:0;
    f.wake();
    for (unsigned i=0;i<5;++i) { now+=100; f.press(); f.client->CheckKeypressTimeout(); }
    require(f.device.inactive==0,"held wake must retain native on intent");
    require(f.shutdownKeys()==0,"held wake must not leak shutdown callback");
    now+=10; f.release();
    require(f.shutdownKeys()==0,"held wake release remains activation-only");
    for(const auto& k:f.client->keys)
      require(k.keycode==CEC_USER_CONTROL_CODE_POWER_ON_FUNCTION,"all wake callbacks retain intent");
  } else if (name=="held-off") {
    f.press();
    for (unsigned i=0;i<4;++i) { now+=100; f.press(); }
    require(f.device.activations==0,"held off must not toggle back on");
    require(f.shutdownKeys()>0,"active source power-off callback preserved");
    f.release();
  } else if (name=="release-then-off") {
    f.wake(); now+=100; f.release(); f.client->keys.clear(); now+=100; f.press();
    require(f.shutdownKeys()>0,"next independent active power press works");
  } else if (name=="lost-release" || name=="timeout-poll") {
    f.wake(); f.client->keys.clear(); now+=CEC_BUTTON_TIMEOUT+1;
    if (name=="timeout-poll") f.client->CheckKeypressTimeout();
    f.press();
    require(f.shutdownKeys()>0,"missing release cannot indefinitely suppress next power");
  } else if (name=="ordinary-replacement") {
    f.wake(); f.press(CEC_USER_CONTROL_CODE_UP); f.press();
    require(f.shutdownKeys()>0,"replacement key ends wake episode");
    bool up=false; for(const auto& k:f.client->keys) up |= k.keycode==CEC_USER_CONTROL_CODE_UP;
    require(up,"ordinary input survives");
  } else if (name=="different-sender") {
    f.wake(); f.press(CEC_USER_CONTROL_CODE_POWER,CECDEVICE_AUDIOSYSTEM);
    require(f.shutdownKeys()>0,"sender must not inherit another sender's wake intent");
  } else if (name=="different-destination") {
    f.wake(); CCECBusDevice other(CECDEVICE_PLAYBACKDEVICE1,f.client);
    f.processor.other=&other; f.press(CEC_USER_CONTROL_CODE_POWER,CECDEVICE_TV,other.address);
    require(f.shutdownKeys()>0,"shared client destination must not inherit wake intent");
  } else if (name=="different-key") {
    f.wake(); f.press(CEC_USER_CONTROL_CODE_POWER_TOGGLE_FUNCTION);
    require(f.shutdownKeys()>0,"different raw key starts new episode");
  } else if (name=="foreign-release" || name=="foreign-destination-release") {
    f.wake(); now+=100;
    CCECBusDevice other(CECDEVICE_PLAYBACKDEVICE1,f.client);
    f.processor.other=&other;
    if(name=="foreign-release") f.release(CECDEVICE_AUDIOSYSTEM);
    else f.release(CECDEVICE_TV,other.address);
    now+=100; f.press();
    require(f.shutdownKeys()==0,"foreign release cannot change held wake intent");
    require(f.device.inactive==0,"foreign release cannot turn activation into native off");
    f.release();
  } else if (name=="suspend-off" || name=="suspend-off-cached-on") {
    f.press(); f.client->keys.clear();
    suspendedMs+=60000; now+=10;
    f.device.active=false;
    f.device.power=name=="suspend-off"?CEC_POWER_STATUS_STANDBY:CEC_POWER_STATUS_ON;
    f.press();
    require(f.device.active && f.device.activations==1,"wake after suspend activates retained client");
    require(f.shutdownKeys()==0,"pre-sleep off or release cannot leak into wake");
  } else if (name=="suspend-wake-then-off") {
    f.wake(); f.client->keys.clear(); suspendedMs+=60000; now+=10; f.press();
    require(f.shutdownKeys()>0,"post-resume independent runtime power is not masked by old wake");
  } else if (name=="suspend-ordinary" || name=="suspend-release" ||
             name=="suspend-direct-key" || name=="suspend-direct-release" ||
             name=="suspend-timeout") {
    f.client->m_configuration.iButtonRepeatRateMs=100;
    f.press(); now+=100; f.press(); now+=100; f.press();
    f.client->keys.clear(); suspendedMs+=60000; now+=10;
    if(name=="suspend-ordinary") f.press(CEC_USER_CONTROL_CODE_UP);
    else if(name=="suspend-release") f.release();
    else if(name=="suspend-direct-key") {
      cec_keypress key; key.keycode=CEC_USER_CONTROL_CODE_UP; key.duration=100;
      f.client->AddKey(key);
    } else if(name=="suspend-direct-release") f.client->AddKey(false,true);
    else f.client->CheckKeypressTimeout();
    require(f.shutdownKeys()==0,"post-suspend input cannot flush stale power callback");
    if(name=="suspend-ordinary" || name=="suspend-direct-key")
      require(f.client->keys.size()==1 && f.client->keys[0].keycode==CEC_USER_CONTROL_CODE_UP,
              "post-suspend ordinary input preserved exactly");
    else require(f.client->keys.empty(),"expired post-suspend release or repeat discarded");
  } else if (name=="vendor-replacement") {
    f.wake(); cec_keypress key; key.keycode=CEC_USER_CONTROL_CODE_UP; key.duration=0;
    f.client->AddKey(key); now+=100; f.release();
    require(f.client->keys.back().keycode==CEC_USER_CONTROL_CODE_UP &&
            f.client->keys.back().duration==100,"direct vendor replacement release survives");
    f.press(); require(f.shutdownKeys()>0,"vendor replacement ends prior wake intent");
  } else if (name=="vendor-power-on") {
    f.wake(); now+=100;
    cec_keypress key; key.keycode=CEC_USER_CONTROL_CODE_POWER_ON_FUNCTION;
    key.duration=CEC_BUTTON_TIMEOUT; f.client->AddKey(key);
    f.client->keys.clear(); now+=20; f.press();
    require(f.shutdownKeys()>0,"explicit vendor on ends prior raw toggle episode");
  } else if (name=="foreign-vendor-release" || name=="vendor-menu-release") {
    CRLCommandHandler rl(&f.processor);
    cec_command c;
    cec_command::Format(c,CECDEVICE_AUDIOSYSTEM,f.device.address,CEC_OPCODE_VENDOR_COMMAND_WITH_ID);
    c.PushBack(0); c.PushBack(0); c.PushBack(0x39);
    if(name=="foreign-vendor-release") {
      f.wake(); c.PushBack(CEC_OPCODE_USER_CONTROL_RELEASE); now+=100;
      require(rl.HandleDeviceVendorCommandWithId(c)==COMMAND_HANDLED,"vendor release handled");
      now+=100; f.press();
      require(f.shutdownKeys()==0,"foreign vendor release cannot end held TV wake");
    } else {
      f.client->m_configuration.iButtonRepeatRateMs=100;
      c.PushBack(CEC_OPCODE_USER_CONTROL_PRESSED); c.PushBack(RL_KEY_TOP_MENU);
      require(rl.HandleDeviceVendorCommandWithId(c)==COMMAND_HANDLED,"vendor menu press handled");
      now+=100; c.parameters.Clear();
      c.PushBack(0); c.PushBack(0); c.PushBack(0x39); c.PushBack(CEC_OPCODE_USER_CONTROL_RELEASE);
      require(rl.HandleDeviceVendorCommandWithId(c)==COMMAND_HANDLED,"vendor menu release handled");
      require(f.client->keys.size()==2 && f.client->keys[1].keycode==CEC_USER_CONTROL_CODE_TOP_MENU &&
              f.client->keys[1].duration==100,"legacy vendor release policy preserved with repeats enabled");
    }
  } else if (name=="clock-fallback") {
    bootClockWorks=false; f.wake(); now+=100; f.press(); now+=100; f.release();
    require(f.shutdownKeys()==0,"existing-clock fallback retains ordinary awake held behavior");
    now+=100; f.press(); require(f.shutdownKeys()>0,"fallback does not block next runtime power");
  } else if (name=="clock-mode-change") {
    f.press(); f.client->keys.clear(); now+=10; bootClockWorks=false;
    f.device.active=false; f.device.power=CEC_POWER_STATUS_STANDBY; f.press();
    require(f.device.active && f.shutdownKeys()==0,"clock mode change cancels old held episode");
  } else if (name=="retire-initialised" || name=="retire-registered") {
    f.wake();
    if(name=="retire-initialised") { f.client->SetInitialised(false); f.client->SetInitialised(true); }
    else { f.client->SetRegistered(false); f.client->SetRegistered(true); }
    f.client->keys.clear(); f.press();
    require(f.shutdownKeys()>0,"new connection cannot inherit held wake classification");
  } else if (name=="retire-off") {
    f.press(); f.client->SetInitialised(false); f.client->keys.clear();
    f.device.active=false; f.device.power=CEC_POWER_STATUS_STANDBY;
    f.client->SetInitialised(true); f.press();
    require(f.shutdownKeys()==0,"old off release must not leak into reconnected wake");
  } else if (name=="matrix") {
    for(auto key:{CEC_USER_CONTROL_CODE_POWER,CEC_USER_CONTROL_CODE_POWER_TOGGLE_FUNCTION})
      for(auto status:{CEC_POWER_STATUS_ON,CEC_POWER_STATUS_STANDBY,
                      CEC_POWER_STATUS_IN_TRANSITION_STANDBY_TO_ON,
                      CEC_POWER_STATUS_IN_TRANSITION_ON_TO_STANDBY,CEC_POWER_STATUS_UNKNOWN})
        for(bool active:{false,true}) {
          Fixture g; g.device.power=status; g.device.active=active;
          const bool on=!active || !(status==CEC_POWER_STATUS_ON ||
                               status==CEC_POWER_STATUS_IN_TRANSITION_STANDBY_TO_ON);
          g.press(key);
          require((g.shutdownKeys()==0)==on,"power-state/source matrix matches intent");
          require(g.device.activations==unsigned(on),"native matrix action matches callback");
        }
  } else if (name=="explicit-power") {
    for(bool active:{false,true}) {
      Fixture g; g.device.active=active;
      g.press(CEC_USER_CONTROL_CODE_POWER_OFF_FUNCTION);
      require(g.shutdownKeys()==1,"explicit power off always delivered");
      Fixture h; h.device.active=active;
      h.press(CEC_USER_CONTROL_CODE_POWER_ON_FUNCTION);
      require(h.shutdownKeys()==0 && h.device.activations==1,"explicit power on activates");
      require(h.client->keys.size()==1 && h.client->keys[0].keycode==CEC_USER_CONTROL_CODE_POWER_ON_FUNCTION &&
              h.client->keys[0].duration==0,"explicit on press callback unchanged");
      now+=100; h.release();
      require(h.client->keys.size()==2 && h.client->keys[1].keycode==CEC_USER_CONTROL_CODE_POWER_ON_FUNCTION &&
              h.client->keys[1].duration==100,"explicit on release callback unchanged");
    }
  } else if (name=="explicit-interruption") {
    f.wake(); f.press(CEC_USER_CONTROL_CODE_POWER_ON_FUNCTION); f.press();
    require(f.shutdownKeys()>0,"explicit on ends prior raw toggle episode");
    Fixture g; g.wake(); g.press(CEC_USER_CONTROL_CODE_POWER_OFF_FUNCTION);
    require(g.shutdownKeys()>0,"explicit off overrides held wake immediately");
  } else if (name=="combo") {
    f.client->m_configuration.comboKey=CEC_USER_CONTROL_CODE_STOP;
    f.client->m_configuration.iComboKeyTimeoutMs=1000;
    f.press(CEC_USER_CONTROL_CODE_STOP); now+=20; f.press(CEC_USER_CONTROL_CODE_SELECT);
    bool exit=false; for(const auto& k:f.client->keys) exit |= k.keycode==CEC_USER_CONTROL_CODE_EXIT;
    require(exit,"existing STOP+SELECT combo preserved");
  } else if (name=="validation") {
    auto c=f.command(CEC_USER_CONTROL_CODE_POWER);
    f.processor.initialised=false;
    require(f.handler.HandleUserControlPressed(c)==CEC_ABORT_REASON_NOT_IN_CORRECT_MODE_TO_RESPOND,
            "uninitialised command rejected");
    f.processor.initialised=true; c.parameters.Clear();
    require(f.handler.HandleUserControlPressed(c)==CEC_ABORT_REASON_INVALID_OPERAND,"empty key rejected");
    c=f.command(CEC_USER_CONTROL_CODE_POWER,CECDEVICE_TV,CECDEVICE_PLAYBACKDEVICE1);
    require(f.handler.HandleUserControlPressed(c)==CEC_ABORT_REASON_NOT_IN_CORRECT_MODE_TO_RESPOND,
            "foreign destination rejected");
    require(f.client->keys.empty(),"invalid commands have no callback");
  } else require(false,"unknown case");
}
int main(int argc,char**argv) {
  require(argc==2,"case required"); test(argv[1]);
  std::cout<<"PASS "<<argv[1]<<" checks="<<checks<<'\n';
}
'''

CASES = ('lg-replay', 'held-wake', 'held-wake-repeat', 'held-off',
         'release-then-off', 'lost-release', 'timeout-poll', 'ordinary-replacement',
         'different-sender', 'different-destination', 'different-key',
         'retire-initialised', 'retire-registered', 'retire-off', 'matrix',
         'explicit-power', 'explicit-interruption', 'combo', 'validation',
         'foreign-release', 'foreign-destination-release', 'suspend-off',
         'suspend-off-cached-on', 'suspend-wake-then-off', 'clock-fallback', 'clock-mode-change',
         'suspend-ordinary', 'suspend-release', 'suspend-direct-key',
         'suspend-direct-release', 'suspend-timeout', 'vendor-replacement', 'vendor-power-on',
         'foreign-vendor-release', 'vendor-menu-release')


def generate(source):
    src = source / 'src/libcec'
    client = (src / 'CECClient.cpp').read_text()
    header = (src / 'CECClient.h').read_text()
    handler = (src / 'implementations/CECCommandHandler.cpp').read_text()
    lg = (src / 'implementations/SLCommandHandler.cpp').read_text()
    rl = (src / 'implementations/RLCommandHandler.cpp').read_text()
    corrected = 'bool CCECClient::SetCurrentPowerButton(' in client
    state = block(header, 'struct PowerButtonState', True) + '\nPowerButtonState m_powerButton;' if corrected else ''
    decl = ('bool SetCurrentPowerButton(const cec_command&,bool); void ResetKeypress(); '
            'void ReleaseCurrentButton(const cec_command&,bool=true); void ExpirePowerButton();') if corrected else ''
    result = PREAMBLE.replace('@STATE@', state).replace('@POWER_DECL@', decl)
    if corrected:
        result += '\n' + block(client, 'static int64_t GetPowerButtonTimeMs(')
    markers = ('void CCECClient::AddKey(bool', 'void CCECClient::AddKey(const',
               'void CCECClient::SetCurrentButton(', 'uint16_t CCECClient::CheckKeypressTimeout(',
               'void CCECClient::SetInitialised(', 'void CCECClient::SetRegistered(')
    for marker in markers:
        result += '\n' + block(client, marker)
    if corrected:
        for marker in ('bool CCECClient::SetCurrentPowerButton(', 'void CCECClient::ResetKeypress(',
                       'void CCECClient::ReleaseCurrentButton(', 'void CCECClient::ExpirePowerButton('):
            result += '\n' + block(client, marker)
    for marker in ('int CCECCommandHandler::HandleUserControlPressed(',
                   'int CCECCommandHandler::HandleUserControlRelease('):
        result += '\n' + block(handler, marker)
    for marker in ('void CSLCommandHandler::HandleVendorCommandSLInit(',
                   'void CSLCommandHandler::HandleVendorCommandPowerOnStatus(',
                   'void CSLCommandHandler::TransmitVendorCommandSLAckInit(',
                   'void CSLCommandHandler::SetSLInitialised('):
        result += '\n' + block(lg, marker)
    result += '\n' + block(rl, 'int CRLCommandHandler::HandleDeviceVendorCommandWithId(')
    return result + TESTS


def replace_once(source, old, new):
    assert source.count(old) == 1, (old, source.count(old))
    return source.replace(old, new, 1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--p8', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--upstream-press', type=Path)
    args = parser.parse_args()
    out = args.output.resolve(); out.mkdir(parents=True, exist_ok=True)
    include = out / 'include'; include.mkdir(exist_ok=True)
    link = include / 'p8-platform'
    if not link.exists(): link.symlink_to((args.p8 / 'src').resolve(), target_is_directory=True)
    env = dict(os.environ, ASAN_OPTIONS='detect_leaks=0', UBSAN_OPTIONS='halt_on_error=1')
    receipts = []

    def run_variant(name, source, cases, expect_success):
        cpp = out / (name + '.cpp'); cpp.write_text(source)
        binary = out / name
        command = [os.environ.get('CXX', 'c++'), '-std=c++11', '-O0', '-g', '-Wall', '-Wextra',
                   '-pthread', '-I'+str(include), '-I'+str(args.source.resolve() / 'include'),
                   '-fsanitize=address,undefined', '-fno-omit-frame-pointer', '-fno-pie', '-no-pie',
                   str(cpp), '-o', str(binary)]
        compiled = subprocess.run(command, capture_output=True, text=True, timeout=60)
        (out / (name+'.compile.log')).write_text(compiled.stdout+compiled.stderr)
        if compiled.returncode:
            raise RuntimeError(name+' compile failed: '+compiled.stderr[-4000:])
        for case in cases:
            run = subprocess.run([str(binary),case],env=env,capture_output=True,text=True,timeout=10)
            if ('AddressSanitizer' in run.stderr or 'runtime error:' in run.stderr):
                raise RuntimeError(name+' unexpected sanitizer failure: '+run.stderr)
            passed = run.returncode == 0
            receipts.append(dict(variant=name,case=case,returncode=run.returncode,
                                 stdout=run.stdout,stderr=run.stderr))
            if passed != expect_success:
                raise RuntimeError(name+'/'+case+' unexpected outcome: '+run.stdout+run.stderr)
        print(name, len(cases), 'expected outcomes', flush=True)

    baseline = generate(args.baseline.resolve())
    candidate = generate(args.source.resolve())
    run_variant('baseline', baseline, ('lg-replay',), False)
    run_variant('candidate', candidate, CASES, True)
    controls = (
        ('raw-wake', 'key.keycode = bPowerOn ? CEC_USER_CONTROL_CODE_POWER_ON_FUNCTION : button;',
         'key.keycode = button;', 'lg-replay'),
        ('stateless-hold', 'if (m_powerButton.key == button &&', 'if (false && m_powerButton.key == button &&', 'held-wake'),
        ('no-suspend-expiry', 'm_powerButton.releaseTime <= powerTime ||',
         'false ||', 'suspend-off-cached-on'),
        ('no-clock-mode-fence', 'm_powerButton.suspendAware != suspendAware ||',
         'false ||', 'clock-mode-change'),
        ('no-sender', 'm_powerButton.initiator == command.initiator &&', 'true &&', 'different-sender'),
        ('no-destination', 'm_powerButton.destination == command.destination &&', 'true &&', 'different-destination'),
        ('no-raw-key', 'm_powerButton.key == button &&', 'true &&', 'different-key'),
        ('no-source-state', 'bPowerOn = !device->IsActiveSource() ||', 'bPowerOn = false ||', 'matrix'),
        ('all-power-on', 'bPowerOn = !device->IsActiveSource() ||', 'bPowerOn = true ||', 'matrix'),
        ('no-retirement-reset', 'm_powerButton = PowerButtonState();\n  m_iCurrentButton = CEC_USER_CONTROL_CODE_UNKNOWN;',
         'return;\n  m_iCurrentButton = CEC_USER_CONTROL_CODE_UNKNOWN;', 'retire-initialised'),
        ('no-held-key-retirement', 'm_iCurrentButton = CEC_USER_CONTROL_CODE_UNKNOWN;\n  m_initialButtontime = 0;',
         '/* retained held button */\n  m_initialButtontime = 0;', 'retire-off'),
        ('off-rewritten', 'else\n      client->SetCurrentButton(key);',
         'else\n      client->SetCurrentButton(CEC_USER_CONTROL_CODE_POWER_ON_FUNCTION);', 'explicit-power'),
        ('no-release-fence', 'if (m_powerButton.key != CEC_USER_CONTROL_CODE_UNKNOWN && m_iCurrentButton == button &&',
         'if (false && m_powerButton.key != CEC_USER_CONTROL_CODE_UNKNOWN && m_iCurrentButton == button &&', 'foreign-release'),
        ('drop-on-callbacks', 'QueueAddKey(transmitKey);',
         'if (transmitKey.keycode != CEC_USER_CONTROL_CODE_POWER_ON_FUNCTION) QueueAddKey(transmitKey);', 'lg-replay'),
        ('no-vendor-boundary', 'ExpirePowerButton();\n    m_powerButton = PowerButtonState();\n    if (m_configuration.iComboKeyTimeoutMs',
         'ExpirePowerButton();\n    if (m_configuration.iComboKeyTimeoutMs', 'vendor-power-on'),
        ('vendor-release-bypass', 'client->ReleaseCurrentButton(command, false);',
         'client->AddKey();', 'foreign-vendor-release'),
    )
    for name, old, new, case in controls:
        run_variant(name,replace_once(candidate,old,new),(case,),False)
    if args.upstream_press:
        old = block(candidate,'int CCECCommandHandler::HandleUserControlPressed(')
        raw = args.upstream_press.read_text()
        replacement = block(raw,'int CCECCommandHandler::HandleUserControlPressed(')
        upstream = replace_once(candidate,old,replacement)
        run_variant('upstream-wake',upstream,('lg-no-shutdown',),True)
        run_variant('upstream-repeat',upstream,('held-wake',),False)
    hashes = {str(p.relative_to(args.source)):hashlib.sha256(p.read_bytes()).hexdigest()
              for p in (args.source/'src/libcec/CECClient.cpp',args.source/'src/libcec/CECClient.h',
                        args.source/'src/libcec/implementations/CECCommandHandler.cpp',
                        args.source/'src/libcec/implementations/RLCommandHandler.cpp')}
    (out/'results.json').write_text(json.dumps(dict(hashes=hashes,receipts=receipts),indent=2)+'\n')
    print('PASS:',len(CASES),'candidate cases;',len(controls),'semantic controls; baseline reproduced')
    print('LIMIT: native bus/activation, callback delivery and clock are host adapters; no target/device claim.')


if __name__ == '__main__':
    main()
