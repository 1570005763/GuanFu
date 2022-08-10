fn main() -> shadow_rs::SdResult<()> {
    println!("cargo:rustc-link-search=native=/lib64");
    println!("cargo:rustc-link-lib=dylib=sgx_urts");

    shadow_rs::new()
}