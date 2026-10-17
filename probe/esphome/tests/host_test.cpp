// Host-side harness for the HomePlug engine (no ESP32 needed).
//
// Replays a scenario file (see make_scenarios.py) against hp::Engine through a
// scripted transport with a simulated clock and prints one JSON line per
// "call". run_compat.py runs the very same scenario through the Python
// implementation and compares the two outputs.
//
//   g++ -std=c++17 -I../components/powerline_probe host_test.cpp ../components/powerline_probe/homeplug.cpp -o host_test
//   ./host_test scenarios/broadcom_two.txt
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iostream>
#include <map>
#include <sstream>
#include <string>
#include <vector>

#include "homeplug.h"

using namespace esphome::powerline_probe::hp;

static Bytes from_hex(const std::string &h) {
  Bytes out;
  for (size_t i = 0; i + 1 < h.size(); i += 2)
    out.push_back(static_cast<uint8_t>(std::stoi(h.substr(i, 2), nullptr, 16)));
  return out;
}

struct Reply {
  uint32_t delay;
  Bytes frame;
};
struct Rule {
  uint16_t ethertype, mmtype;
  std::string dst;  // "any", "bcast" or 12 hex digits
  int at = -1;      // optional: frame bytes at this offset must equal atbytes
  Bytes atbytes;
  std::vector<Reply> replies;
};
struct Pib {
  Mac mac;
  Bytes data;
};
struct Queued {
  uint32_t avail;
  uint64_t order;
  Bytes frame;
};

class Scripted : public Transport {
 public:
  Mac own{};
  std::vector<Rule> rules;
  std::vector<Pib> pibs;
  uint32_t clock = 1000;
  std::map<uint16_t, std::vector<Queued>> q;
  std::map<uint16_t, bool> opened;
  uint64_t order = 0;

  void open(uint16_t et) override {
    q[et].clear();
    opened[et] = true;
  }
  void close() override {
    q.clear();
    opened.clear();
  }
  Mac own_mac() override { return own; }
  uint32_t now_ms() override { return clock; }

  void enqueue(uint16_t et, uint32_t delay, const Bytes &f) {
    if (opened[et])
      q[et].push_back({clock + delay, order++, f});
  }

  bool send(const uint8_t *f, size_t n) override {
    uint16_t et = static_cast<uint16_t>((f[12] << 8) | f[13]);
    uint16_t mmtype = static_cast<uint16_t>(f[15] | (f[16] << 8));
    Mac dst;
    std::memcpy(dst.data(), f, 6);
    for (auto &r : rules) {
      if (r.ethertype != et || r.mmtype != mmtype)
        continue;
      if (r.dst == "bcast") {
        if (dst != BROADCAST_MAC)
          continue;
      } else if (r.dst != "any") {
        if (mac_to_str(dst) != r.dst)
          continue;
      }
      if (r.at >= 0) {
        if (n < r.at + r.atbytes.size() || std::memcmp(f + r.at, r.atbytes.data(), r.atbytes.size()) != 0)
          continue;
      }
      for (auto &rp : r.replies)
        enqueue(et, rp.delay, rp.frame);
    }
    // synthetic PIB server for module-op reads (MMV 0, MMTYPE 0xA0B0)
    if (et == 0x88E1 && mmtype == 0xA0B0 && f[14] == 0x00 && n >= 41) {
      for (auto &p : pibs) {
        if (p.mac != dst)
          continue;
        const uint8_t *hdr = f + 20;
        uint32_t clen = hdr[17] | (hdr[18] << 8);
        uint32_t off = hdr[19] | (hdr[20] << 8);
        if (off + clen > p.data.size())
          continue;
        Bytes r;
        r.insert(r.end(), own.begin(), own.end());
        r.insert(r.end(), p.mac.begin(), p.mac.end());
        r.push_back(0x88);
        r.push_back(0xE1);
        r.push_back(0x00);
        r.push_back(0xB1);
        r.push_back(0xA0);
        r.push_back(0x00);
        r.push_back(0xB0);
        r.push_back(0x52);
        Bytes pl(27, 0);
        pl[23] = off & 0xFF;
        pl[24] = (off >> 8) & 0xFF;
        pl[25] = (off >> 16) & 0xFF;
        pl[26] = (off >> 24) & 0xFF;
        r.insert(r.end(), pl.begin(), pl.end());
        r.insert(r.end(), p.data.begin() + off, p.data.begin() + off + clen);
        enqueue(et, 0, r);
      }
    }
    return true;
  }

  size_t recv(uint16_t et, uint8_t *buf, size_t cap, uint32_t timeout_ms) override {
    auto &v = q[et];
    int best = -1;
    for (size_t i = 0; i < v.size(); i++) {
      if (best < 0 || v[i].avail < v[best].avail || (v[i].avail == v[best].avail && v[i].order < v[best].order))
        best = static_cast<int>(i);
    }
    if (best >= 0 && v[best].avail <= clock + timeout_ms) {
      if (v[best].avail > clock)
        clock = v[best].avail;
      Bytes f = v[best].frame;
      v.erase(v.begin() + best);
      size_t n = f.size() < cap ? f.size() : cap;
      std::memcpy(buf, f.data(), n);
      return n;
    }
    clock += timeout_ms;
    return 0;
  }
};

static Mac parse_mac(const std::string &s) {
  Mac m;
  if (!mac_from_str(s, m)) {
    std::fprintf(stderr, "bad mac %s\n", s.c_str());
    std::exit(2);
  }
  return m;
}

int main(int argc, char **argv) {
  if (argc < 2) {
    std::fprintf(stderr, "usage: host_test scenario.txt\n");
    return 2;
  }
  std::ifstream in(argv[1]);
  if (!in) {
    std::fprintf(stderr, "cannot open %s\n", argv[1]);
    return 2;
  }
  Scripted t;
  Engine engine(&t);
  Rule *cur = nullptr;
  std::string line;
  while (std::getline(in, line)) {
    if (line.empty() || line[0] == '#')
      continue;
    std::istringstream ls(line);
    std::string cmd;
    ls >> cmd;
    if (cmd == "own") {
      std::string m;
      ls >> m;
      t.own = parse_mac(m);
    } else if (cmd == "on") {
      std::string et, mm, dst;
      ls >> et >> mm >> dst;
      Rule r;
      std::string opt;
      if (ls >> opt && opt.rfind("at=", 0) == 0) {
        size_t colon = opt.find(':');
        r.at = std::stoi(opt.substr(3, colon - 3));
        r.atbytes = from_hex(opt.substr(colon + 1));
      }
      r.ethertype = static_cast<uint16_t>(std::stoi(et, nullptr, 16));
      r.mmtype = static_cast<uint16_t>(std::stoi(mm, nullptr, 16));
      for (auto &c : dst)
        c = static_cast<char>(std::toupper(c));
      r.dst = (dst == "ANY") ? "any" : (dst == "BCAST") ? "bcast" : mac_to_str(parse_mac(dst));
      t.rules.push_back(r);
      cur = &t.rules.back();
    } else if (cmd == "reply") {
      std::string delay, src, et, body;
      ls >> delay >> src >> et >> body;
      Reply rp;
      rp.delay = static_cast<uint32_t>(std::stoul(delay));
      Mac s = parse_mac(src);
      rp.frame.insert(rp.frame.end(), t.own.begin(), t.own.end());
      rp.frame.insert(rp.frame.end(), s.begin(), s.end());
      Bytes e = from_hex(et);
      rp.frame.insert(rp.frame.end(), e.begin(), e.end());
      Bytes b = from_hex(body);
      rp.frame.insert(rp.frame.end(), b.begin(), b.end());
      if (cur == nullptr) {
        std::fprintf(stderr, "reply without rule\n");
        return 2;
      }
      cur->replies.push_back(rp);
    } else if (cmd == "pib") {
      std::string m, size, fill;
      ls >> m >> size >> fill;
      Pib p;
      p.mac = parse_mac(m);
      p.data.assign(std::stoul(size, nullptr, 16), static_cast<uint8_t>(std::stoi(fill, nullptr, 16)));
      t.pibs.push_back(p);
    } else if (cmd == "pibset") {
      std::string m, off, hex;
      ls >> m >> off >> hex;
      Mac mm = parse_mac(m);
      Bytes b = from_hex(hex);
      for (auto &p : t.pibs)
        if (p.mac == mm)
          for (size_t i = 0; i < b.size(); i++)
            p.data[std::stoul(off, nullptr, 16) + i] = b[i];
    } else if (cmd == "call") {
      std::string what;
      ls >> what;
      if (what == "discover") {
        double timeout;
        ls >> timeout;
        std::vector<Device> devs;
        std::vector<Link> links;
        engine.discover(timeout, devs, links);
        std::printf("%s\n", Engine::discover_json(devs, links).c_str());
      } else if (what == "states") {
        std::string list;
        ls >> list;
        std::vector<std::string> macs;
        std::stringstream ss(list);
        std::string item;
        while (std::getline(ss, item, ',')) {
          for (auto &c : item)
            c = static_cast<char>(std::toupper(c));
          if (!item.empty())
            macs.push_back(item);
        }
        std::printf("%s\n", Engine::states_json(engine.query_states(macs)).c_str());
      }
    }
  }
  return 0;
}
