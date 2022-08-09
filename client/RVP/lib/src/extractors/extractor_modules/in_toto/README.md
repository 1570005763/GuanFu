# In-toto Extractor

This Extractor verifies all `.link` files using in-toto 
[verify-lib](https://github.com/in-toto/in-toto-golang/blob/master/in_toto/verifylib.go).

## Format of Provenance

The format of in-toto provenance in a `Message` is as the following
```json
{
    "version" : "VERSION OF IN-TOTO",
    "line_normalization" : true/false,
    "files" : {
        "FILE_PATH" : "BASE64 ENCODED CONTENT",
        ...
    }
}
```

Here,
* `files` includes all `.link`, `.pub` and `.layout` files, with file path
set as `"FILE_PATH"` (e.g., `keys/key1.pub` indicates `key1.pub` is in the 
directory `keys/`), and content encoded in base64 `"BASE64 ENCODED CONTENT"`.
* `line_normalization` indicates whether line separators like CRLF in Windows
should be all converted to LF, to avoid cross-platform compatibility when
calculating digest.
* `version` indicates the version of this in-toto provenance. By default, 
the `version` will be `0.9`.

## Format of the Reference Value

The Reference Value generated by in-toto extractor will also be as Reference Value
```json
{
    "version" : "<REFERENCE_VALUE_VERSION>",
    "name" : "<NAME-OF-THE-ARTIFACT>",
    "hash-value" : [
        {
            "alg": "<HASH-ALGORITHM>",
            "value": "<HASH-VALUE>"
        },
        ...
    ],
    "expired":"<EXPIRED-TIME>"
}
```

## TODO
`expired` field needs to be extract from provenance.