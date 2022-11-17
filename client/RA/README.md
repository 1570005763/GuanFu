# Attestation Client

This is AC for PoC.

## Set up

Install llvm & clang

```bash
apt install clang llvm 
```

Install sgx-sdk & librats

## Basic Principle

When Attestation Client does a remote attestation, it will firstly
send a request to server inside the TEE.

On the serverside, the server will use a pre-defined `report data`
as the input to run `get_evidence()`. The function will generate
the quote (evidence), which consists of different hardware information,
software information, report data, and a hardware-signature for them.

Together with eventlog (whose digest is also contained and signed in the quote),
quote is sent to Attestation Client.

Attestation Client will then get reference value information from
the Reference Value Provider Service, where reproducible build
occurs that Guanfu works.

Finally, Attestation Client calls `verify_evidence()` to compare the
reference value and the gathered evidence.

## Launch AC

```bash
cargo run -- --as-addr <EGS-ADDR:PORT> --rvps-addr <RVPS-ADDR:PORT>
```