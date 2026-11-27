#!/usr/bin/env python3
"""Exercise the patched AOCEC method and kernel saved-address helpers on the host.

Requires unpacked libCEC and the matching kernel source. Hardware register access,
ioctls and adapter locking are stubbed; this is not a device wake test.
"""
import argparse
import os
from pathlib import Path
import subprocess
import tempfile

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--libcec-source', type=Path, required=True)
parser.add_argument('--kernel-source', type=Path, required=True)
args = parser.parse_args()
relative = Path('src/libcec/adapter/AOCEC/AOCECAdapterCommunication.cpp')
original = (args.libcec_source / relative).read_text()
api = (args.kernel_source / 'drivers/amlogic/cec/hdmi_aocec_api.c').read_text()
driver = (args.kernel_source / 'drivers/amlogic/cec/hdmi_ao_cec.c').read_text()
patch = Path(__file__).resolve().parents[1] / 'patches/libcec-004-aocec-replace-primary-address.patch'


def function(source, signature):
    start = source.index(signature)
    return source[start:source.index('\n}', start) + 2] + '\n'


preamble = r'''
#include <cassert>
#include <cstdio>
#include <cstring>
#include "cectypes.h"
using namespace CEC;
typedef unsigned int u32;
enum { CEC_A, CEC_B, ENABLE_ONE_CEC=1, CEC_PW_POWER_ON=0,
       AO_DEBUG_REG1=0,
       CEC_IOC_CLR_LOGICAL_ADDR=1, CEC_IOC_ADD_LOGICAL_ADDR=2 };
static unsigned int sticky, hardware[2];
static bool ee_cec;
static struct Device {
  unsigned int cec_num;
  struct { unsigned int addr_enable, log_addr, power_status; } cec_info;
} device, *cec_dev=&device;
static unsigned int read_ao(unsigned int) { return sticky; }
static void cec_set_reg_bits(unsigned int, unsigned int value, unsigned int start, unsigned int len) {
  unsigned int mask=((1u<<len)-1)<<start;
  sticky=(sticky & ~mask) | ((value<<start)&mask);
}
#define CEC_INFO(...) ((void)0)
static void cec_clear_all_logical_addr(unsigned int sel) { hardware[sel]=0; }
static void ceca_addr_add(unsigned int addr) { hardware[CEC_A] |= 1u<<addr; }
static void cecb_addr_add(unsigned int addr) { hardware[CEC_B] |= 1u<<addr; }
'''
helpers = ''.join(function(api, name) for name in (
    'unsigned int cec_config2_logaddr(', 'void cec_clear_saved_logic_addr(',
    'void cec_logicaddr_add(', 'void cec_ap_clear_logical_addr(',
    'void cec_ap_add_logical_addr('))
# Use the actual two ioctl cases, including the saved-address clear operation.
start = driver.index('\tcase CEC_IOC_ADD_LOGICAL_ADDR:')
cases = driver[start:driver.index('\tcase CEC_IOC_SET_DEV_TYPE:', start)]
adapter = r'''
static unsigned int calls, clear_calls, add_calls, fail_op;
static int ioctl(int, unsigned int cmd, unsigned long arg) {
  calls++;
  if (cmd==CEC_IOC_CLR_LOGICAL_ADDR) clear_calls++;
  if (cmd==CEC_IOC_ADD_LOGICAL_ADDR) add_calls++;
  if (cmd==fail_op) return -1;
  switch(cmd) {
''' + cases + r'''
  default: assert(false);
  }
  return 0;
}
static cec_logical_addresses address_set(unsigned int primary, unsigned int secondary=15) {
  cec_logical_addresses addresses; addresses.Clear();
  if (primary<15) addresses.Set((cec_logical_address)primary);
  if (secondary<15) addresses.Set((cec_logical_address)secondary);
  return addresses;
}
struct CLockObject {
  int &m;
  explicit CLockObject(int &mutex):m(mutex) { assert(m++==0); }
  ~CLockObject() { assert(--m==0); }
};
struct Logger { void AddLog(int, const char *, const char *) {} } logger;
#define LIB_CEC (&logger)
struct CAOCECAdapterCommunication {
  int m_mutex=0, m_fd=1;
  bool open=true, m_bLogicalAddressChanged=false;
  cec_logical_addresses m_logicalAddresses=address_set(15);
  bool IsOpen() const { return open; }
  bool SetLogicalAddresses(const cec_logical_addresses &addresses);
};
'''
tests = r'''
static int checks, failures;
#define CHECK(x) do { checks++; if (!(x)) { fprintf(stderr,"line %d: %s\n",__LINE__,#x); failures++; } } while(0)
static void reset() {
  sticky=0xa0502100; // preserve physical address, device type and unrelated top bits
  memset(&device,0,sizeof(device)); device.cec_num=ENABLE_ONE_CEC;
  hardware[0]=hardware[1]=0; ee_cec=CEC_A;
  calls=clear_calls=add_calls=fail_op=0;
}
static void expect_primary(unsigned int primary) {
  CHECK(((sticky>>16)&15)==primary);
  CHECK(((sticky>>24)&15)==0);
  CHECK((sticky & 0xf0f0ffff)==0xa0502100);
  CHECK(cec_dev->cec_info.addr_enable==(1u<<primary));
  CHECK(hardware[ee_cec]==(1u<<primary));
}
int main() {
  reset(); CAOCECAdapterCommunication a;
  CHECK(a.SetLogicalAddresses(address_set(1))); expect_primary(1);
  CHECK(a.SetLogicalAddresses(address_set(4))); expect_primary(4); // same-session replacement
  CHECK(a.SetLogicalAddresses(address_set(4))); expect_primary(4); // repeat
  CHECK(a.SetLogicalAddresses(address_set(15))); // unregister must keep first wake slot
  CHECK(((sticky>>16)&15)==4);
  // SYS_CTRL close/open resets allocation but deliberately retains wake slots.
  cec_dev->cec_info.addr_enable=0; cec_clear_all_logical_addr(ee_cec);
  cec_config2_logaddr(15,true); // kernel open
  CHECK(a.SetLogicalAddresses(address_set(1))); expect_primary(1);
  CHECK(a.SetLogicalAddresses(address_set(15)));
  cec_dev->cec_info.addr_enable=0; cec_clear_all_logical_addr(ee_cec);
  CHECK(((sticky>>16)&15)==1); // saved address survives shutdown
  CHECK((sticky & 0xf0f0ffff)==0xa0502100);

  reset(); a.open=false;
  CHECK(!a.SetLogicalAddresses(address_set(4))); CHECK(calls==0); a.open=true;
  CHECK(a.SetLogicalAddresses(address_set(1))); a.m_bLogicalAddressChanged=false;
  fail_op=CEC_IOC_CLR_LOGICAL_ADDR; unsigned int before_add=add_calls;
  CHECK(!a.SetLogicalAddresses(address_set(4))); CHECK(add_calls==before_add);
  CHECK(a.m_logicalAddresses.primary==1); CHECK(!a.m_bLogicalAddressChanged);
  expect_primary(1);
  fail_op=CEC_IOC_ADD_LOGICAL_ADDR;
  CHECK(!a.SetLogicalAddresses(address_set(4))); CHECK(hardware[ee_cec]==0);
  CHECK(cec_dev->cec_info.addr_enable==0);
  CHECK(a.m_logicalAddresses.primary==1); CHECK(!a.m_bLogicalAddressChanged);
  fail_op=0; CHECK(a.SetLogicalAddresses(address_set(4))); expect_primary(4);

  // Valid TV and secondary playback addresses, and the second controller.
  for (unsigned int sel=0;sel<2;sel++) {
    reset(); ee_cec=sel;
    CHECK(a.SetLogicalAddresses(address_set(0))); expect_primary(0);
    CHECK(a.SetLogicalAddresses(address_set(8))); expect_primary(8);
  }
  // Adding/removing a second libCEC client must replace the complete mask.
  reset(); CHECK(a.SetLogicalAddresses(address_set(4))); expect_primary(4);
  CHECK(a.SetLogicalAddresses(address_set(1,4)));
  CHECK(hardware[CEC_A]==((1u<<1)|(1u<<4)));
  CHECK(cec_dev->cec_info.addr_enable==((1u<<1)|(1u<<4)));
  CHECK(((sticky>>16)&15)==1); CHECK(((sticky>>24)&15)==4);
  CHECK(a.m_logicalAddresses.IsSet(CECDEVICE_RECORDINGDEVICE1));
  CHECK(a.m_logicalAddresses.IsSet(CECDEVICE_PLAYBACKDEVICE1));
  CHECK(a.SetLogicalAddresses(address_set(4))); expect_primary(4);
  // Primary order is preserved even if another address has a lower number.
  CHECK(a.SetLogicalAddresses(address_set(4,1)));
  CHECK(((sticky>>16)&15)==4); CHECK(((sticky>>24)&15)==1);
  // Kernel ADD still supports genuine multiple-address consumers.
  reset(); cec_ap_add_logical_addr(1); cec_ap_add_logical_addr(5);
  CHECK(((sticky>>16)&15)==1); CHECK(((sticky>>24)&15)==5);
  CHECK(hardware[CEC_A]==((1u<<1)|(1u<<5)));
  printf("%d assertions, %d failures\n",checks,failures);
  return failures ? 1 : 0;
}
'''

with tempfile.TemporaryDirectory(prefix='aocec-addresses-') as directory:
    root = Path(directory)
    target = root / relative
    target.parent.mkdir(parents=True)
    target.write_text(original)
    subprocess.run(['patch', '--batch', '--fuzz=0', '-p1', '-i', str(patch)], cwd=root, check=True)
    for label, source in [('baseline', original), ('patched', target.read_text())]:
        method = function(source, 'bool CAOCECAdapterCommunication::SetLogicalAddresses(')
        code = root / (label + '.cpp')
        code.write_text(preamble + helpers + adapter + method + tests)
        binary = root / label
        subprocess.run([os.environ.get('CXX', 'c++'), '-std=c++11', '-Wall', '-Wextra',
                        '-Werror', '-I', str(args.libcec_source / 'include'), str(code), '-o', str(binary)], check=True)
        result = subprocess.run([str(binary)], text=True, capture_output=True)
        print(label + ': ' + result.stdout.strip())
        if label == 'baseline':
            if result.returncode != 1:
                raise SystemExit('Expected the original adapter to fail regression assertions')
        elif result.returncode:
            raise SystemExit(result.stderr)
