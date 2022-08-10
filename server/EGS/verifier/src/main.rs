use std::fs;
use lib::{verify_evidence, AttestationEv};
use eventlog_rs::Eventlog;
use anyhow::*;

pub const REPORT_DATA: &[u8] = b"test";

#[tokio::main]
async fn main() -> Result<()> {
    let quote = fs::read("../../fixtures/quote")?;
    let eventlog_info = fs::read("../../fixtures/eventlog_info")?;
    let eventlog_data = fs::read("../../fixtures/eventlog_data")?;

    let evidence = AttestationEv {
        quote,
        eventlog_info,
        eventlog_data,
    };

    let eventlog = verify_evidence("tdx", evidence, REPORT_DATA).await?;

    println!("{:?}", eventlog);

    Ok(())
}
