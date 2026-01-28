#!/usr/bin/env python3
"""
Buildspec校验脚本：验证buildspec.aml文件中outputs的path是否都是绝对路径
"""

import yaml
import os
import sys
from pathlib import Path


def validate_buildspec(file_path):
    """
    校验buildspec文件中inputs的targetPath和outputs的path是否都是绝对路径
    
    Args:
        file_path (str): buildspec文件路径
    
    Returns:
        bool: 校验是否通过
    """
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            buildspec_data = yaml.safe_load(f)
    except FileNotFoundError:
        print(f"错误: 文件 {file_path} 不存在")
        return False
    except yaml.YAMLError as e:
        print(f"错误: 解析YAML文件失败 - {e}")
        return False
    
    # 检查inputs中的targetPath是否为绝对路径
    inputs = buildspec_data.get('inputs', {})  # 改成默认 {}

    all_valid = True
    for name, input_item in inputs.items():
        if isinstance(input_item, dict) and 'targetPath' in input_item:
            target_path = input_item['targetPath']
            if not os.path.isabs(target_path):
                print(f"错误: inputs['{name}'].targetPath '{target_path}' 不是绝对路径")
                all_valid = False
        else:
            print(f"警告: inputs['{name}'] 中没有targetPath字段")
    
    # 检查outputs中的path是否为绝对路径
    outputs = buildspec_data.get('outputs', [])
    
    if not outputs:
        print("警告: buildspec中没有outputs部分")
        return all_valid
    
    for i, output in enumerate(outputs):
        if 'path' in output:
            path = output['path']
            # 检查路径是否为绝对路径
            if not os.path.isabs(path):
                print(f"错误: outputs[{i}].path '{path}' 不是绝对路径")
                all_valid = False
        else:
            print(f"警告: outputs[{i}] 中没有path字段")
    
    return all_valid


def main():
    if len(sys.argv) != 2:
        print("用法: python validate_buildspec.py <buildspec_file_path>")
        sys.exit(1)
    
    buildspec_file = sys.argv[1]
    
    print(f"正在校验文件: {buildspec_file}")
    
    if validate_buildspec(buildspec_file):
        print("校验通过: 所有outputs中的path都是绝对路径")
        sys.exit(0)
    else:
        print("校验失败: 存在非绝对路径的outputs.path")
        sys.exit(1)


if __name__ == "__main__":
    main()
