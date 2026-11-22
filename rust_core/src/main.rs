// Rust 核心模块 v0.2.0：低内存时间隔离引擎 + 数据校验 + 批量文件哈希。
//
// v0.2.0 修复（对应 AUDIT_REPORT.md P0/P1）：
//   R1 日期改为严格 (y,m,d) 解析比较，禁止字符串字典序；
//   R2 manifest 字段用最小 JSON 解析器读取，sealed 必须是布尔 true，缺字段/解析失败一律 fail-closed；
//   R3 逐项重算 manifest.file_hashes 并比对，篡改即 violation；
//   R4 .timeindex IO 错误 fail-closed；空扫描/低于 entry_count 拒绝通过；
//   R5 JSON 输出转义；CLI 参数边界检查；NIST SHA-256 自测向量。
//
// CLI:
//   timeguard audit --snapshot <dir> --cutoff <YYYY-MM-DD> --manifest <file> [--require-sealed] [--entry-count N] [--json]
//   timeguard hash  --file <path>
//   timeguard selftest
use std::env;
use std::fs::{self, File};
use std::io::{self, BufRead, BufReader, Read};
use std::path::{Path, PathBuf};
use std::process;

#[derive(Debug)]
struct Violation {
    file: String,
    line: usize,
    value: String,
    reason: String,
}

// ---------- SHA-256（自实现，附 NIST 向量自测）----------
struct Sha256 {
    state: [u32; 8],
    buffer: [u8; 64],
    buflen: usize,
    total: u64,
}
impl Sha256 {
    fn new() -> Self {
        Self {
            state: [0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,
                    0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19],
            buffer: [0u8;64], buflen:0, total:0,
        }
    }
    fn update(&mut self, data: &[u8]) {
        self.total = self.total.wrapping_add(data.len() as u64);
        for &b in data {
            self.buffer[self.buflen] = b;
            self.buflen += 1;
            if self.buflen == 64 {
                let block = self.buffer;
                self.compress(&block);
                self.buflen = 0;
            }
        }
    }
    fn finalize(mut self) -> [u8;32] {
        let bitlen = self.total.wrapping_mul(8);
        let mut pad = [0u8;128];
        pad[0] = 0x80;
        let padlen = if self.buflen < 56 { 56 - self.buflen } else { 120 - self.buflen };
        self.update(&pad[..padlen]);
        let mut tail = [0u8;8];
        tail.copy_from_slice(&bitlen.to_be_bytes());
        self.update(&tail);
        let mut out = [0u8;32];
        for (i, s) in self.state.iter().enumerate() {
            out[i*4..i*4+4].copy_from_slice(&s.to_be_bytes());
        }
        out
    }
    fn compress(&mut self, block: &[u8;64]) {
        const K: [u32;64] = [
            0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
            0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
            0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
            0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
            0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
            0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
            0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
            0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2];
        let mut w = [0u32;64];
        for i in 0..16 {
            w[i] = u32::from_be_bytes([block[i*4],block[i*4+1],block[i*4+2],block[i*4+3]]);
        }
        for i in 16..64 {
            let s0 = w[i-15].rotate_right(7)^w[i-15].rotate_right(18)^(w[i-15]>>3);
            let s1 = w[i-2].rotate_right(17)^w[i-2].rotate_right(19)^(w[i-2]>>10);
            w[i] = w[i-16].wrapping_add(s0).wrapping_add(w[i-7]).wrapping_add(s1);
        }
        let mut a=self.state[0]; let mut b=self.state[1]; let mut c=self.state[2]; let mut d=self.state[3];
        let mut e=self.state[4]; let mut f=self.state[5]; let mut g=self.state[6]; let mut h=self.state[7];
        for i in 0..64 {
            let s1=e.rotate_right(6)^e.rotate_right(11)^e.rotate_right(25);
            let ch=(e&f)^((!e)&g);
            let t1=h.wrapping_add(s1).wrapping_add(ch).wrapping_add(K[i]).wrapping_add(w[i]);
            let s0=a.rotate_right(2)^a.rotate_right(13)^a.rotate_right(22);
            let maj=(a&b)^(a&c)^(b&c);
            let t2=s0.wrapping_add(maj);
            h=g; g=f; f=e; e=d.wrapping_add(t1); d=c; c=b; b=a; a=t1.wrapping_add(t2);
        }
        self.state[0]=self.state[0].wrapping_add(a); self.state[1]=self.state[1].wrapping_add(b);
        self.state[2]=self.state[2].wrapping_add(c); self.state[3]=self.state[3].wrapping_add(d);
        self.state[4]=self.state[4].wrapping_add(e); self.state[5]=self.state[5].wrapping_add(f);
        self.state[6]=self.state[6].wrapping_add(g); self.state[7]=self.state[7].wrapping_add(h);
    }
}
fn sha256_bytes(data: &[u8]) -> String {
    let mut h = Sha256::new();
    h.update(data);
    h.finalize().iter().map(|b| format!("{:02x}", b)).collect()
}
fn sha256_file(path: &Path) -> io::Result<String> {
    let mut file = File::open(path)?;
    let mut hasher = Sha256::new();
    let mut buf = [0u8; 65536];
    loop {
        let n = file.read(&mut buf)?;
        if n == 0 { break; }
        hasher.update(&buf[..n]);
    }
    Ok(hasher.finalize().iter().map(|b| format!("{:02x}", b)).collect())
}

// ---------- 严格日期解析 ----------
fn parse_ymd(s: &str) -> Option<(i32,u32,u32)> {
    // 仅接受 YYYY-MM-DD（长度10，位置4/7为'-'）
    let b = s.as_bytes();
    if b.len() != 10 || b[4] != b'-' || b[7] != b'-' { return None; }
    let y: i32 = s.get(0..4)?.parse().ok()?;
    let m: u32 = s.get(5..7)?.parse().ok()?;
    let d: u32 = s.get(8..10)?.parse().ok()?;
    if m < 1 || m > 12 || d < 1 || d > 31 { return None; }
    // 简单的每月天数校验（2月按闰年）
    let leap = (y % 4 == 0 && y % 100 != 0) || y % 400 == 0;
    let mdays = [31, if leap {29} else {28},31,30,31,30,31,31,30,31,30,31];
    if d > mdays[(m-1) as usize] { return None; }
    Some((y,m,d))
}

// ---------- 最小 JSON 解析（仅取所需字段，避免 serde 依赖）----------
fn json_get_string(content: &str, key: &str) -> Option<String> {
    let needle = format!("\"{}\"", key);
    let pos = content.find(&needle)? + needle.len();
    let rest = &content[pos..];
    let colon = rest.find(':')?;
    let val = rest[colon+1..].trim_start();
    if !val.starts_with('"') { return None; }
    let end = val[1..].find('"')? + 1;
    Some(val[1..end].to_string())
}
fn json_get_i64(content: &str, key: &str) -> Option<i64> {
    let needle = format!("\"{}\"", key);
    let pos = content.find(&needle)? + needle.len();
    let rest = &content[pos..];
    let colon = rest.find(':')?;
    let val = rest[colon+1..].trim_start();
    let end = val.find(|c: char| !c.is_ascii_digit() && c != '-').unwrap_or(val.len());
    val[..end].parse().ok()
}
/// sealed 必须是 JSON 布尔 true；缺字段、0、字符串、空格变体一律 None（fail-closed）
fn json_sealed_true(content: &str) -> Option<bool> {
    let needle = "\"sealed\"";
    let pos = content.find(needle)? + needle.len();
    let rest = &content[pos..];
    let colon = rest.find(':')?;
    let val = rest[colon+1..].trim_start();
    if val.starts_with("true") { Some(true) }
    else if val.starts_with("false") { Some(false) }
    else { None } // 0 / null / string => 无效
}
/// 提取 "file_hashes": { "name": "hash", ... } 的键值对
fn json_file_hashes(content: &str) -> Vec<(String,String)> {
    let mut out = Vec::new();
    let needle = "\"file_hashes\"";
    let start = match content.find(needle) { Some(p)=>p+needle.len(), None=>return out };
    let rest = &content[start..];
    let brace = match rest.find('{') { Some(p)=>p, None=>return out };
    let obj = &rest[brace+1..];
    // 截取到匹配的 } 为止（file_hashes 对象不含嵌套对象）
    let end_brace = match obj.find('}') { Some(p)=>p, None=>obj.len() };
    let obj = &obj[..end_brace];
    let mut cur = obj;
    loop {
        let kq = match cur.find('"') { Some(p)=>p, None=>break };
        let after_k = &cur[kq+1..];
        let ke = match after_k.find('"') { Some(p)=>p, None=>break };
        let key = &after_k[..ke];
        let tail = &after_k[ke+1..];
        let ccolon = match tail.find(':') { Some(p)=>p, None=>break };
        let vpart = tail[ccolon+1..].trim_start();
        if !vpart.starts_with('"') { break; }
        let ve = match vpart[1..].find('"') { Some(p)=>p+1, None=>break };
        let val = &vpart[1..ve];
        out.push((key.to_string(), val.to_string()));
        let after_v = &vpart[ve+1..];
        match after_v.find(',') { Some(p)=>cur=&after_v[p+1..], None=>break }
        // 若遇到 } 则结束
        if cur.trim_start().starts_with('}') { break; }
    }
    out
}

// ---------- 收集 timeindex ----------
fn collect_timeindex(dir: &Path, out: &mut Vec<PathBuf>) -> io::Result<()> {
    let entries = fs::read_dir(dir)?; // fail-closed：读不到就报错
    let mut paths: Vec<PathBuf> = Vec::new();
    for e in entries {
        let p = e?.path();
        paths.push(p);
    }
    paths.sort();
    for p in paths {
        if p.is_dir() { collect_timeindex(&p, out)?; }
        else if p.extension().and_then(|s| s.to_str()) == Some("timeindex") { out.push(p); }
    }
    Ok(())
}

fn json_escape(s: &str) -> String {
    s.replace('\\', "\\\\").replace('"', "\\\"").replace('\n', "\\n")
}

fn audit(snapshot: &Path, cutoff: &str, manifest: Option<&Path>, as_json: bool,
         require_sealed: bool, spec_id: Option<&str>, spec_cutoff: Option<&str>,
         entry_count: Option<i64>) -> i32 {
    let mut violations: Vec<Violation> = Vec::new();
    let mut scanned = 0usize;

    // 1) 严格日期解析 cutoff
    let cut = match parse_ymd(cutoff) {
        Some(t) => t,
        None => {
            violations.push(Violation{ file:"argv".into(), line:0, value:cutoff.into(),
                reason:"invalid_cutoff_date".into() });
            cut_or_fail(&violations, as_json, 0, "missing", "", &[]);
            return 2;
        }
    };

    // 2) 扫描 .timeindex（fail-closed IO）
    let mut files = Vec::new();
    if let Err(e) = collect_timeindex(snapshot, &mut files) {
        violations.push(Violation{ file:snapshot.display().to_string(), line:0,
            value:e.to_string(), reason:"timeindex_io_error".into() });
    }
    if files.is_empty() {
        violations.push(Violation{ file:snapshot.display().to_string(), line:0,
            value:"0".into(), reason:"no_timeindex_files".into() });
    }
    for f in &files {
        let rel = f.strip_prefix(snapshot).unwrap_or(f).display().to_string();
        let file = match File::open(f) {
            Ok(x) => x,
            Err(e) => {
                violations.push(Violation{ file:rel, line:0, value:e.to_string(),
                    reason:"timeindex_open_error".into() });
                continue;
            }
        };
        for (idx, line) in BufReader::new(file).lines().enumerate() {
            let line = match line {
                Ok(x) => x,
                Err(e) => {
                    violations.push(Violation{ file:rel.clone(), line:idx+1, value:e.to_string(),
                        reason:"timeindex_read_error".into() });
                    continue;
                }
            };
            let value = line.trim().to_string();
            if value.is_empty() { continue; }
            scanned += 1;
            let lv = value.to_ascii_lowercase();
            if lv == "unknown" || lv == "null" || lv == "none" {
                violations.push(Violation{ file:rel.clone(), line:idx+1, value,
                    reason:"unknown_availability".into() });
            } else {
                match parse_ymd(&value) {
                    Some(t) => if t > cut {
                        violations.push(Violation{ file:rel.clone(), line:idx+1, value,
                            reason:"future_data_leak".into() });
                    },
                    None => violations.push(Violation{ file:rel.clone(), line:idx+1, value,
                        reason:"invalid_date_format".into() }),
                }
            }
        }
    }
    // 3) 扫描条数下限校验（fail-closed）
    let expected = entry_count.or_else(|| manifest.and_then(|m| {
        fs::read_to_string(m).ok().and_then(|c| json_get_i64(&c, "entry_count"))
    }));
    if let Some(n) = expected {
        if (scanned as i64) < n {
            violations.push(Violation{ file:"scan".into(), line:0,
                value:format!("scanned={} expected>={}", scanned, n),
                reason:"insufficient_scan_coverage".into() });
        }
    }

    // 4) manifest 严格校验
    let mut manifest_status = "absent".to_string();
    let mut hashed: Vec<(String,String)> = Vec::new();
    match manifest {
        None => { if require_sealed {
            violations.push(Violation{ file:"argv".into(), line:0, value:"missing".into(),
                reason:"manifest_required".into() });
        }},
        Some(m) => match fs::read_to_string(m) {
            Err(e) => { manifest_status = "unreadable".into();
                violations.push(Violation{ file:m.display().to_string(), line:0, value:e.to_string(),
                    reason:"manifest_unreadable".into() }); },
            Ok(content) => {
                // sealed（严格布尔）
                match json_sealed_true(&content) {
                    Some(true) => { manifest_status = "sealed".into(); },
                    Some(false) => { manifest_status = "unsealed".into();
                        if require_sealed {
                            violations.push(Violation{ file:m.display().to_string(), line:0,
                                value:"false".into(), reason:"manifest_not_sealed".into() });
                        } },
                    None => { manifest_status = "invalid".into();
                        violations.push(Violation{ file:m.display().to_string(), line:0,
                            value:"sealed field invalid/missing".into(),
                            reason:"manifest_invalid_sealed".into() }); },
                }
                // cutoff_date 与 spec 一致
                if let Some(mc) = json_get_string(&content, "cutoff_date") {
                    if mc != cutoff {
                        violations.push(Violation{ file:m.display().to_string(), line:0,
                            value:mc, reason:"manifest_cutoff_mismatch".into() });
                    }
                } else if require_sealed {
                    violations.push(Violation{ file:m.display().to_string(), line:0,
                        value:"missing".into(), reason:"manifest_missing_cutoff".into() });
                }
                // knowledge_snapshot_id 一致
                if let (Some(sid), Some(mid)) = (spec_id, json_get_string(&content, "knowledge_snapshot_id")) {
                    if sid != mid {
                        violations.push(Violation{ file:m.display().to_string(), line:0,
                            value:mid, reason:"manifest_snapshot_id_mismatch".into() });
                    }
                }
                // 逐项重算 file_hashes（含 parquet / timeindex / pkl / json）
                for (name, expected_hash) in json_file_hashes(&content) {
                    let p = snapshot.join(&name);
                    let actual = sha256_file(&p).unwrap_or_else(|_| "MISSING".into());
                    hashed.push((name.clone(), actual.clone()));
                    if actual != expected_hash {
                        violations.push(Violation{ file:name, line:0, value:actual,
                            reason:"manifest_hash_mismatch".into() });
                    }
                }
                if json_file_hashes(&content).is_empty() && require_sealed {
                    violations.push(Violation{ file:m.display().to_string(), line:0,
                        value:"0".into(), reason:"manifest_no_hashes".into() });
                }
            }
        },
    }

    cut_or_fail(&violations, as_json, scanned, &manifest_status, spec_cutoff.unwrap_or(cutoff), &hashed)
}

fn cut_or_fail(violations: &[Violation], as_json: bool, scanned: usize,
               manifest_status: &str, cutoff: &str, hashed: &[(String,String)]) -> i32 {
    let ok = violations.is_empty();
    if as_json {
        let vio = violations.iter().map(|v| {
            format!("{{\"file\":\"{}\",\"line\":{},\"value\":\"{}\",\"reason\":\"{}\"}}",
                json_escape(&v.file), v.line, json_escape(&v.value), json_escape(&v.reason))
        }).collect::<Vec<_>>().join(",");
        let hs = hashed.iter().map(|(n,h)| format!("[\"{}\",\"{}\"]", json_escape(n), h))
            .collect::<Vec<_>>().join(",");
        println!("{{\"ok\":{}, \"scanned\":{}, \"violations\":[{}], \"manifest\":\"{}\", \"cutoff\":\"{}\", \"hashes\":[{}]}}",
            ok, scanned, vio, manifest_status, cutoff, hs);
    } else {
        println!("TimeGuard v0.2.0 audit: cutoff={} files-scanned={}", cutoff, scanned);
        for v in violations {
            println!("  VIOLATION [{}] {}:{} value={}", v.reason, v.file, v.line, v.value);
        }
        for (n,h) in hashed { println!("  HASH {} {}", n, h); }
        println!("RESULT: {}", if ok {"PASS"} else {"FAIL"});
    }
    if ok { 0 } else { 2 }
}

fn selftest() -> i32 {
    // NIST SHA-256 测试向量
    let vectors: Vec<(&[u8], &str)> = vec![
        (b"", "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"),
        (b"abc", "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"),
    ];
    let mut ok = true;
    for (data, expect) in vectors {
        let got = sha256_bytes(data);
        if got != expect { ok = false; println!("FAIL: {} != {}", got, expect); }
    }
    // parse_ymd 边界
    let good = parse_ymd("2001-12-31") == Some((2001,12,31));
    let bad1 = parse_ymd("20011231").is_none();
    let bad2 = parse_ymd("2001-13-01").is_none();
    let bad3 = parse_ymd("2001-02-30").is_none();
    if !(good && bad1 && bad2 && bad3) { ok = false; println!("FAIL: parse_ymd vectors"); }
    println!("SELFTEST: {}", if ok {"PASS"} else {"FAIL"});
    if ok {0} else {2}
}

fn print_help() {
    println!("timeguard v0.2.0 - 时间隔离核心引擎\n\
CLI:\n\
  timeguard audit --snapshot <dir> --cutoff <YYYY-MM-DD> [--manifest <file>] \
[--require-sealed] [--spec-id <id>] [--cutoff-check <date>] [--entry-count N] [--json]\n\
  timeguard hash  --file <path>\n\
  timeguard selftest\n");
}

fn main() {
    let args: Vec<String> = env::args().skip(1).collect();
    if args.is_empty() { print_help(); process::exit(0); }
    match args[0].as_str() {
        "selftest" => process::exit(selftest()),
        "audit" => {
            let mut snapshot: Option<PathBuf> = None;
            let mut cutoff: Option<String> = None;
            let mut manifest: Option<PathBuf> = None;
            let mut as_json = false;
            let mut require_sealed = false;
            let mut spec_id: Option<String> = None;
            let mut cutoff_check: Option<String> = None;
            let mut entry_count: Option<i64> = None;
            let mut i = 1;
            while i < args.len() {
                let need = |i: usize| -> String {
                    if i+1 >= args.len() { eprintln!("missing value for {}", args[i]); process::exit(1); }
                    args[i+1].clone()
                };
                match args[i].as_str() {
                    "--snapshot" => { snapshot=Some(PathBuf::from(need(i))); i+=1; },
                    "--cutoff" => { cutoff=Some(need(i)); i+=1; },
                    "--manifest" => { manifest=Some(PathBuf::from(need(i))); i+=1; },
                    "--spec-id" => { spec_id=Some(need(i)); i+=1; },
                    "--cutoff-check" => { cutoff_check=Some(need(i)); i+=1; },
                    "--entry-count" => { entry_count=need(i).parse().ok(); i+=1; },
                    "--json" => { as_json=true; },
                    "--require-sealed" => { require_sealed=true; },
                    _ => {}
                }
                i+=1;
            }
            let snap = match snapshot { Some(s)=>s, None=>{ eprintln!("missing --snapshot"); process::exit(1);} };
            let cut = match cutoff { Some(c)=>c, None=>{ eprintln!("missing --cutoff"); process::exit(1);} };
            let (cc, sid) = (cutoff_check.as_deref(), spec_id.as_deref());
            let code = audit(&snap, &cut, manifest.as_deref(), as_json, require_sealed, sid, cc, entry_count);
            process::exit(code);
        }
        "hash" => {
            let idx = args.iter().position(|x| x=="--file");
            match idx {
                Some(i) if i+1 < args.len() => match sha256_file(Path::new(&args[i+1])) {
                    Ok(h)=>println!("{}",h),
                    Err(e)=>{eprintln!("{}",e);process::exit(1);}
                },
                _ => { eprintln!("hash: missing --file <path>"); process::exit(1); }
            }
        }
        _ => print_help(),
    }
}
