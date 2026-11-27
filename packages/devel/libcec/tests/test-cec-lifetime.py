#!/usr/bin/env python3
"""Bounded corrected libCEC lifetime checks, extracted from production source.

Includes unmodified P8 thread/event/buffer headers. Native processor/hardware
operations are adapters; scheduling/destruction observations are explicit fixture
instrumentation. No faulty baseline, mutation controls or long-drain demonstration.
ASan/UBSan enabled, leak detection unavailable under sandbox restrictions.
"""
import argparse
import os
from pathlib import Path
import re
import subprocess
import sys


def block(text, marker, semicolon=False):
    start = text.index(marker)
    opening = text.index("{", start)
    # The selected classes/functions contain no braces in strings/comments.
    level = 1
    end = opening + 1
    while level:
        level += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[start:end] + (";" if semicolon else "")


PREAMBLE = r'''
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdlib>
#include <functional>
#include <iostream>
#include <map>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <vector>
#include "cectypes.h"
#include "p8-platform/threads/mutex.h"
using namespace CEC;
using namespace P8PLATFORM;
void require(bool ok, const char* message) {
  if (!ok) { std::cerr << "FAIL: " << message << std::endl; std::_Exit(1); }
}
struct Gate {
  std::mutex mutex;
  std::condition_variable condition;
  bool entered = false, released = false;
  void pause() {
    std::unique_lock<std::mutex> lock(mutex);
    entered = true; condition.notify_all();
    condition.wait(lock, [this] { return released; });
  }
  void await() {
    std::unique_lock<std::mutex> lock(mutex);
    require(condition.wait_for(lock, std::chrono::seconds(3),
            [this] { return entered; }), "schedule checkpoint not reached");
  }
  void release() {
    std::lock_guard<std::mutex> lock(mutex);
    released = true; condition.notify_all();
  }
};
// Only the native entry trampoline is gated. Actual P8 CThread handles every
// running/stopped predicate, condition wait, receipt and completion transition.
Gate* startupGate = nullptr;
struct NativeEntry { void* (*handler)(void*); void* argument; Gate* gate; };
void* nativeEntry(void* raw) {
  std::unique_ptr<NativeEntry> entry(static_cast<NativeEntry*>(raw));
  if (entry->gate) entry->gate->pause();
  return entry->handler(entry->argument);
}
bool fixtureThreadsCreate(thread_t& thread, void* (*handler)(void*), void* arg) {
  auto entry = new NativeEntry{handler, arg, startupGate};
  if (pthread_create(&thread, GetDetachedThreadAttribute(), nativeEntry, entry)) {
    delete entry; return false;
  }
  return true;
}
#undef ThreadsCreate
#define ThreadsCreate(thread, handler, arg) fixtureThreadsCreate(thread, handler, arg)
#include "p8-platform/threads/threads.h"
#undef ThreadsCreate
#include "p8-platform/util/buffer.h"
#define SAFE_DELETE(p) do { delete (p); (p) = NULL; } while (0)
std::atomic<int> responseDestroyed{0}, wrapDestroyed{0};
std::atomic<bool> stopCallbacksEntered{false};
Gate* borrowGate = nullptr;
thread_local bool pauseBorrow = false;
void borrowCheckpoint() { if (pauseBorrow && borrowGate) borrowGate->pause(); }
'''

ADAPTERS = r'''
class CCECClient;
typedef std::shared_ptr<CCECClient> CECClientPtr;
std::atomic<bool> allocatorStopObserved{false};
class Worker : public CThread {
public:
  Gate gate;
  std::atomic<bool> completed{false};
  std::atomic<bool>* premature;
  bool allocator;
  explicit Worker(std::atomic<bool>* p, bool a = false) : premature(p), allocator(a) {
    require(CreateThread(), "worker start");
  }
  bool StopThread(int timeout = 5000) override {
    if (allocator) allocatorStopObserved = true;
    return CThread::StopThread(timeout);
  }
  ~Worker() {
    if (!completed) *premature = true;
    // Abort before releasing a live worker's fixture backing state. This
    // lifetime oracle lets negative controls fail without performing a UAF.
    require(completed, "producer destroyed before completed retirement");
    gate.release(); StopThread(0);
  }
  void* Process() override { gate.pause(); completed = true; return nullptr; }
};
struct Communication {
  std::atomic<bool>* retired;
  explicit Communication(std::atomic<bool>* r) : retired(r) {}
  ~Communication() { *retired = true; }
};
class CCECProcessor : public CThread {
public:
  CMutex m_mutex;
  CEvent m_inBuffer;
  Worker *m_connCheck = nullptr, *m_addrAllocator = nullptr;
  Communication* m_communication = nullptr;
  int* m_busDevices = nullptr;
  bool m_bStallCommunication = false, m_bInitialised = true;
  std::atomic<bool> retired{false}, reset{false}, workerReleasedEarly{false};
  std::atomic<bool> processorCompleted{true};
  Gate processorGate;
  bool startSuccess = true, registerSuccess = true;
  int unregisterCalls = 0;
  ~CCECProcessor();
  void Close();
  bool Start(const char*, uint16_t, uint32_t) {
    if (!startSuccess) return false;
    retired = false; reset = false;
    m_communication = new Communication(&retired); return true;
  }
  bool RegisterClient(CECClientPtr);
  void UnregisterClient(CCECClient*);
  void UnregisterClients() { ++unregisterCalls; reset = true; }
  void SetCECInitialised(bool value) {
    CLockObject lock(m_mutex); m_bInitialised = value;
    if (!value) UnregisterClients();
  }
  bool CECInitialised() { return m_bInitialised; }
  void* Process() override {
    processorGate.pause(); processorCompleted = true; return nullptr;
  }
  void startProducer() {
    processorCompleted = false;
    require(CreateThread(), "processor start"); processorGate.await();
  }
};
class CCECClient : public CThread {
public:
  CCECProcessor* m_processor;
  CMutex m_mutex;
  libcec_configuration m_configuration;
  bool m_bInitialised, m_bRegistered;
  cec_user_control_code m_iCurrentButton;
  int64_t m_initialButtontime, m_updateButtontime, m_repeatButtontime;
  int64_t m_releaseButtontime;
  int32_t m_pressedButtoncount, m_releasedButtoncount;
  int64_t m_iPreventForwardingPowerOffCommand;
  @BUFFER@
  std::function<void(const cec_command&)> commandCallback;
  std::function<int(cec_menu_state)> menuCallback;
  CCECClient(CCECProcessor*, const libcec_configuration&);
  ~CCECClient();
  @LIFECYCLE@
  void QueueAddCommand(const cec_command&);
  int QueueMenuStateChanged(cec_menu_state);
  void* Process() override;
  bool IsRegistered() { return m_bRegistered; }
  void SetConfiguration(const libcec_configuration& config) { m_configuration = config; }
  void AddCommand(const cec_command& command) { if (commandCallback) commandCallback(command); }
  int CallbackMenuStateChanged(cec_menu_state state) { return menuCallback ? menuCallback(state) : 7; }
  void CallbackAddLog(const cec_log_message_cpp&) {}
  void CallbackAddKey(const cec_keypress&) {}
  void CallbackAlert(libcec_alert, const libcec_parameter&) {}
  void CallbackConfigurationChanged(const libcec_configuration&) {}
  void CallbackSourceActivated(bool, cec_logical_address) {}
};
bool CCECProcessor::RegisterClient(CECClientPtr client) {
  if (registerSuccess) client->m_bRegistered = true;
  return registerSuccess;
}
void CCECProcessor::UnregisterClient(CCECClient* client) {
  ++unregisterCalls; client->m_bRegistered = false;
}
class CLibCEC {
public:
  CCECProcessor* m_cec = new CCECProcessor;
  CECClientPtr m_client;
  std::vector<CECClientPtr> m_clients;
  ~CLibCEC();
  bool Open(const char*, uint32_t);
  void Close();
  void AddLog(cec_log_level, const char*) {}
  void addClient() {
    libcec_configuration config; config.Clear();
    m_client = std::make_shared<CCECClient>(m_cec, config);
    m_clients.push_back(m_client);
  }
};
'''

TESTS = r'''
void responseTest(bool received) {
  CWaitForResponse wait;
  require(!wait.Wait(CEC_OPCODE_MENU_REQUEST, 20), "unanswered opcode must time out");
  wait.Received(CEC_OPCODE_MENU_REQUEST);
  require(wait.Wait(CEC_OPCODE_MENU_REQUEST, 20), "matching response must wake waiter");
  require(!wait.Wait(CEC_OPCODE_GIVE_DEVICE_POWER_STATUS, 20), "other opcode must stay unanswered");
  wait.Clear(); responseDestroyed = 0;
  Gate gate; borrowGate = &gate;
  std::thread borrower([&] {
    pauseBorrow = true;
    if (received) wait.Received(CEC_OPCODE_MENU_REQUEST);
    else require(wait.Wait(CEC_OPCODE_MENU_REQUEST, 100), "Clear must wake retained waiter");
  });
  gate.await(); wait.Clear();
  require(responseDestroyed == 0, "Clear destroyed a response borrowed by Wait/Received");
  gate.release(); borrower.join();
  require(responseDestroyed == 1, "response not released after borrowed call completes");
}
void startupTest() {
  Gate gate; startupGate = &gate;
  std::atomic<bool> returned{false};
  CCECClient* client = nullptr;
  std::thread create([&] {
    libcec_configuration config; config.Clear();
    client = new CCECClient(nullptr, config); returned = true;
  });
  gate.await(); std::this_thread::sleep_for(std::chrono::milliseconds(100));
  require(!returned, "constructor returned before callback worker startup receipt");
  gate.release(); create.join(); startupGate = nullptr;
  require(client->IsRunning(), "constructor receipt did not establish running worker");
  delete client;
}
void closeTest() {
  stopCallbacksEntered = false;
  CLibCEC lib; lib.addClient(); require(lib.Open("fixture", 100), "initial Open");
  Gate gate;
  std::atomic<bool> callbackCompleted{false}, closeReturned{false};
  lib.m_client->commandCallback = [&](const cec_command&) {
    gate.pause();
    // Callback re-entry needs the processor lock while Close waits.
    CLockObject lock(lib.m_cec->m_mutex);
    require(!lib.m_cec->retired && !lib.m_cec->reset,
            "Close retired processor state before callback re-entry completed");
    callbackCompleted = true;
  };
  cec_command command; command.Clear(); lib.m_client->QueueAddCommand(command); gate.await();
  std::thread close([&] { lib.Close(); closeReturned = true; });
  auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(3);
  while (!stopCallbacksEntered && std::chrono::steady_clock::now() < deadline) std::this_thread::yield();
  require(stopCallbacksEntered, "StopCallbacks entry checkpoint missing");
  require(!closeReturned && !lib.m_cec->retired && !lib.m_cec->reset,
          "Close returned/reset/released native state while callback was executing");
  gate.release(); close.join(); require(callbackCompleted, "current callback did not finish");
  require(!lib.m_client->IsRunning() && lib.m_cec->retired, "Close completion/state release");
  int seen = 0;
  lib.m_client->commandCallback = [&](const cec_command&) { ++seen; };
  lib.m_client->QueueAddCommand(command); // old closed-session event
  require(lib.Open("fixture", 100), "reopen");
  require(lib.m_client->m_callbackCalls.IsEmpty(), "Open retained old closed-session event");
  lib.m_client->QueueAddCommand(command);
  for (int i = 0; i < 100 && !lib.m_client->m_callbackCalls.IsEmpty(); ++i)
    std::this_thread::sleep_for(std::chrono::milliseconds(5));
  lib.Close(); require(seen == 1, "reopen must dispatch only fresh callback");
  lib.Close(); require(!lib.m_client->IsRunning(), "repeated Close restarted worker");
}
void menuTest() {
  CLibCEC lib; lib.addClient(); require(lib.Open("fixture", 100), "menu Open");
  Gate blocker;
  lib.m_client->commandCallback = [&](const cec_command&) { blocker.pause(); };
  cec_command command; command.Clear(); lib.m_client->QueueAddCommand(command); blocker.await();
  wrapDestroyed = 0;
  require(lib.m_client->QueueMenuStateChanged(CEC_MENU_STATE_ACTIVATED) == 0,
          "timed-out menu callback must return neutral result");
  require(wrapDestroyed == 0, "menu timeout deleted result still queued for callback worker");
  std::atomic<int> menuCalled{0};
  lib.m_client->menuCallback = [&](cec_menu_state) { ++menuCalled; return 7; };
  blocker.release();
  for (int i = 0; i < 200 && menuCalled == 0; ++i)
    std::this_thread::sleep_for(std::chrono::milliseconds(5));
  require(menuCalled == 1, "timed-out retained menu callback not dispatched");
  require(lib.m_client->QueueMenuStateChanged(CEC_MENU_STATE_ACTIVATED) == 7,
          "live menu callback result lost");
  lib.Close(); require(wrapDestroyed == 3, "callback wrappers not released exactly once");
}
void processorTest() {
  CCECProcessor processor;
  processor.m_communication = new Communication(&processor.retired);
  processor.startProducer();
  processor.m_connCheck = new Worker(&processor.workerReleasedEarly);
  processor.m_addrAllocator = new Worker(&processor.workerReleasedEarly, true);
  processor.m_connCheck->gate.await(); processor.m_addrAllocator->gate.await();
  std::atomic<bool> closed{false};
  std::thread close([&] { processor.Close(); closed = true; });
  std::this_thread::sleep_for(std::chrono::milliseconds(100));
  require(!closed && !processor.reset && !processor.retired,
          "processor reset/release occurred before actual worker completion");
  require(!processor.workerReleasedEarly, "producer destroyed before completed retirement");
  processor.processorGate.release();
  std::this_thread::sleep_for(std::chrono::milliseconds(50));
  require(!processor.reset && !processor.retired,
          "allocator/checker still active when processor state retired");
  processor.m_connCheck->gate.release();
  // Keep the allocator active until its real P8 StopThread has been entered.
  // The negative adapter aborts before destruction if this call is omitted.
  for (int i = 0; i < 600 && !allocatorStopObserved; ++i)
    std::this_thread::sleep_for(std::chrono::milliseconds(5));
  require(allocatorStopObserved, "allocator completion wait was not entered");
  require(!processor.reset && !processor.retired,
          "allocator still active when processor state retired");
  processor.m_addrAllocator->gate.release(); close.join();
  require(processor.processorCompleted && !processor.workerReleasedEarly,
          "retirement did not wait for producer completion");
  require(processor.m_addrAllocator == nullptr && processor.m_connCheck == nullptr,
          "producer objects retained after Close");
  require(processor.reset && processor.retired, "completed Close did not reset/release state");
}
void detachTest() {
  auto lib = new CLibCEC; lib->addClient();
  require(lib->Open("fixture", 100), "detach Open");
  CECClientPtr retained = lib->m_client;
  delete lib;
  require(retained->m_processor == nullptr, "retained client points at destroyed processor");
  require(!retained->IsRunning(), "retained client callback worker still running");
  retained.reset();
}
void openFailureTest() {
  CLibCEC lib; lib.addClient();
  lib.m_cec->startSuccess = false;
  require(!lib.Open("fixture", 100), "failed native Open reported success");
  require(!lib.m_client->IsRunning(), "failed Open left callback worker running");
  lib.m_cec->startSuccess = true; lib.m_cec->registerSuccess = false;
  require(!lib.Open("fixture", 100), "failed registration reported success");
  require(!lib.m_client->IsRunning() && lib.m_cec->retired,
          "failed registration did not complete callback/native retirement");
  lib.m_cec->registerSuccess = true;
  require(lib.Open("fixture", 100), "successful Open after failures");
  lib.Close();
}
int main(int argc, char** argv) {
  require(argc == 2, "one test name required"); std::string name = argv[1];
  if (name == "response-wait") responseTest(false);
  else if (name == "response-received") responseTest(true);
  else if (name == "startup-receipt") startupTest();
  else if (name == "close-reopen") closeTest();
  else if (name == "menu-timeout") menuTest();
  else if (name == "processor-retirement") processorTest();
  else if (name == "retained-client") detachTest();
  else if (name == "open-failure") openFailureTest();
  else require(false, "unknown test name");
  std::cout << "PASS: " << name << std::endl; return 0;
}
'''


def generate(source):
    def read(path):
        return (source / "src/libcec" / path).read_text()

    bus_h = read("devices/CECBusDevice.h")
    bus_cpp = read("devices/CECBusDevice.cpp")
    client_h = read("CECClient.h")
    client_cpp = read("CECClient.cpp")
    processor_cpp = read("CECProcessor.cpp")
    lib_cpp = read("LibCEC.cpp")
    responses = block(bus_h, "class CResponse\n", True) + "\n"
    responses += block(bus_h, "class CWaitForResponse\n", True) + "\n"
    for marker in ("CResponse::CResponse(", "CResponse::~CResponse(",
                   "bool CResponse::Wait(", "void CResponse::Broadcast(",
                   "CWaitForResponse::CWaitForResponse(", "CWaitForResponse::~CWaitForResponse(",
                   "void CWaitForResponse::Clear(", "bool CWaitForResponse::Wait(",
                   "void CWaitForResponse::Received("):
        excerpt = block(bus_cpp, marker)
        if marker == "CResponse::~CResponse(":
            excerpt = excerpt.replace("{", "{ ++responseDestroyed;", 1)
        if marker in ("bool CWaitForResponse::Wait(", "void CWaitForResponse::Received("):
            excerpt = excerpt.replace("GetEvent(opcode);", "GetEvent(opcode); borrowCheckpoint();", 1)
        responses += excerpt + "\n"
    get_event = re.search(r"[^\n]+CWaitForResponse::GetEvent\(cec_opcode opcode\)", bus_cpp).group()
    responses += block(bus_cpp, get_event) + "\n"
    wrapper = block(client_h, "class CCallbackWrap\n", True)
    wrapper = wrapper.replace("  public:", "  public:\n    ~CCallbackWrap() { ++wrapDestroyed; }", 1)
    buffer_decl = re.search(r"P8PLATFORM::SyncedBuffer<.*>\s+m_callbackCalls;", client_h).group()
    lifecycle = "\n".join("bool " + name + "();" if name == "StartCallbacks" else "void " + name + "();"
                          for name in ("StartCallbacks", "StopCallbacks", "DetachProcessor")
                          if "CCECClient::" + name in client_cpp)
    adapters = ADAPTERS.replace("@BUFFER@", buffer_decl).replace("@LIFECYCLE@", lifecycle)
    functions = ""
    for marker in ("CCECClient::CCECClient(", "CCECClient::~CCECClient(",
                   "bool CCECClient::StartCallbacks(", "void CCECClient::StopCallbacks(",
                   "void CCECClient::DetachProcessor(", "void CCECClient::QueueAddCommand(",
                   "int CCECClient::QueueMenuStateChanged(", "void* CCECClient::Process("):
        if marker in client_cpp:
            excerpt = block(client_cpp, marker)
            if marker == "void CCECClient::StopCallbacks(":
                assert "StopThread(0);" in excerpt, "completed callback retirement required"
                excerpt = excerpt.replace("StopThread(0);", "stopCallbacksEntered = true; StopThread(0);", 1)
            functions += excerpt + "\n"
    for marker in ("CCECProcessor::~CCECProcessor(", "void CCECProcessor::Close("):
        functions += block(processor_cpp, marker) + "\n"
    for marker in ("CLibCEC::~CLibCEC(", "bool CLibCEC::Open(", "void CLibCEC::Close("):
        functions += block(lib_cpp, marker) + "\n"
    log_message = block(read("LibCEC.h"), "struct cec_log_message_cpp", True)

    return PREAMBLE + log_message + responses + wrapper + adapters + functions + TESTS


ALL_TESTS = ("response-wait", "response-received", "startup-receipt", "close-reopen",
             "menu-timeout", "processor-retirement", "retained-client", "open-failure")

def main():
    parser = argparse.ArgumentParser(description="Bounded corrected libCEC contracts with actual P8 headers; no baseline or mutation controls")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--p8", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    out = args.output.resolve(); out.mkdir(parents=True, exist_ok=True)
    include = out / "include"; include.mkdir(exist_ok=True)
    link = include / "p8-platform"
    if not link.exists(): link.symlink_to((args.p8 / "src").resolve(), target_is_directory=True)
    cpp = out / "cec-lifetime.cpp"; cpp.write_text(generate(args.source.resolve()))
    binary = out / "cec-lifetime"
    command = [os.environ.get("CXX", "c++"), "-std=c++11", "-O0", "-g", "-Wall", "-Wextra",
               "-pthread", "-I" + str(include), "-I" + str(args.source.resolve() / "include"),
               "-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-fno-pie", "-no-pie",
               str(cpp), "-o", str(binary)]
    compiled = subprocess.run(command, capture_output=True, text=True, timeout=60)
    (out / "compile.log").write_text(compiled.stdout + compiled.stderr)
    assert compiled.returncode == 0, compiled.stderr
    environment = {**os.environ, "ASAN_OPTIONS": "detect_leaks=0:abort_on_error=1",
                   "UBSAN_OPTIONS": "halt_on_error=1:print_stacktrace=1"}
    for test in ALL_TESTS:
        result = subprocess.run([str(binary), test], capture_output=True, text=True, timeout=15, env=environment)
        (out / (test + ".log")).write_text(result.stdout + result.stderr)
        assert result.returncode == 0, result.stdout + result.stderr
        print(result.stdout.strip(), flush=True)
    print("8/8 corrected libCEC host contracts passed; hardware/complete linkage and leak detection unverified")

if __name__ == "__main__": main()
