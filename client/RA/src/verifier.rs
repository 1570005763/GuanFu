//! verifier

use anyhow::*;
use eventlog_rs::Eventlog;
use lib::AttestationEv;
use reference_value_provider_service::ReferenceValue;

use std::collections::HashSet;
use std::fmt::Write;

pub const REPORT_DATA: &[u8] = "12345678123456781234567812345678".as_bytes();
pub const TEE: &str = "tdx";

pub async fn verify_evidence(
    tee: &str,
    evidence: AttestationEv,
    report_data: &[u8],
) -> Result<Eventlog> {
    lib::verify_evidence(tee, evidence, report_data).await
}

pub fn verify(event_log: Eventlog, rv: ReferenceValue) -> Result<()> {
    let mut verified_digests = HashSet::new();

    for hv in rv.hash_values() {
        verified_digests.insert(hv.value().to_owned());
    }

    let unverified_digests: Vec<String> = event_log
        .log
        .iter()
        .flat_map(|entry| &entry.digests)
        .map(|digest| {
            let mut dig_str = String::with_capacity(2 * digest.digest.len());
            for byte in &digest.digest {
                write!(dig_str, "{:02x}", byte).unwrap();
            }
            dig_str
        })
        .collect();

    for unv_dig in &unverified_digests {
        if verified_digests.contains(unv_dig) {
            return Ok(());
        }
    }

    bail!("Digest match failed!")
}
