#!/usr/bin/env python3

import argparse
import sqlite3
import os
import pandas as pd
from concurrent.futures import ProcessPoolExecutor
from time import perf_counter
from multiprocessing import cpu_count
from functools import partial
from itertools import islice


def cpu_type(x):
    return max(1, min(int(x), cpu_count()))


def take(n, iterable):
    return list(islice(iterable, n))


def get_similarity(con, fp, mol_field, table, query, limit=1):
    threshold = 0.7
    while threshold >= 0:
        sql = f"""SELECT 
                        main.smi, 
                        main.id, 
                        bfp_tanimoto(mol_{fp}_bfp(main.{mol_field}, {'2,' if fp == 'morgan' else ''} 2048), 
                                     mol_{fp}_bfp(mol_from_smiles(?1), {'2,' if fp == 'morgan' else ''} 2048)) as t 
                      FROM 
                        {table} AS main, {table}_{fp}_idx AS idx
                      WHERE 
                        main.rowid = idx.id AND
                        idx.id MATCH rdtree_tanimoto(mol_{fp}_bfp(mol_from_smiles(?1), {'2,' if fp == 'morgan' else ''} 2048), ?2) 
                      ORDER BY t DESC 
                      {'LIMIT ' + str(limit) if limit is not None else ''}"""

        res = con.execute(sql, (query, threshold)).fetchall()
        if res:
            return res
        threshold -= 0.1


def calc_sim_for_smiles(smiles, db_name, fp, mol_field, table):
    with sqlite3.connect(db_name) as con, sqlite3.connect(':memory:') as dest:
        con.backup(dest)
        dest.enable_load_extension(True)
        dest.load_extension('chemicalite')
        dest.enable_load_extension(False)
        all_res = []
        for mol_id, smi in smiles:
            res = get_similarity(dest, fp, mol_field, table, smi, limit=1)
            res = res[0] + (mol_id, smi)
            all_res.append(res)
    return all_res


def main():
    parser = argparse.ArgumentParser(description='Similarity search using the selected fingerprints, '
                                                 'which should be previously added to DB.')
    parser.add_argument('-d', '--input_db', metavar='FILENAME', required=True,
                        help='input SQLite DB.')
    parser.add_argument('-i', '--input_smiles', metavar='FILENAME', required=True,
                        help='input smiles.')
    parser.add_argument('-o', '--output', metavar='FILENAME', required=False, default=None,
                        help='output text file. If omitted output will be printed to STDOUT.')
    parser.add_argument('-t', '--table', metavar='STRING', default='mols',
                        help='table name where Mol objects are stored. Default: mols.')
    parser.add_argument('-m', '--mol_field', metavar='STRING', default='mol',
                        help='field name where mol objects are stored. Default: mol.')
    parser.add_argument('-f', '--fp', metavar='STRING', default='morgan', choices=['morgan', 'pattern'],
                        help='fingerprint type to compute. Default: morgan.')
    parser.add_argument('-p', '--threshold', metavar='NUMERIC', default=0.7, type=float,
                        help='Tanimoto similarity threshold. Default: 0.7.')
    parser.add_argument('-l', '--limit', metavar='INTEGER', default=None, type=int,
                        help='maximum number of matches to retrieve. Default: None.')
    parser.add_argument('-n', '--ncpu', default=1, type=cpu_type,
                        help='number of cpus.')

    args = parser.parse_args()

    if os.path.isfile(args.output):
        os.remove(args.output)

    df_mols = pd.read_csv(args.input_smiles, sep=',')
    smiles = df_mols.smi.to_list()
    mol_ids = df_mols.Name.to_list()
    chunked = iter(partial(take, args.ncpu, iter(zip(mol_ids, smiles))), [])
    start = perf_counter()

    with open(args.output, 'a') as f, ProcessPoolExecutor(max_workers=args.ncpu) as p:
        f.write('\t'.join(['smi', 'mol_id', 'chembl_smi', 'chembl_id', 'similarity']) + '\n')
        for chembl_smi, chembl_id, sim, mol_id, smi_sql in sum(p.map(
                partial(calc_sim_for_smiles, db_name=args.input_db, fp=args.fp, mol_field=args.mol_field, table=args.table),
                chunked), []):
            f.write(f'{smi_sql}\t{mol_id}\t{chembl_smi}\t{chembl_id}\t{round(sim, 4)}\n')

    print(perf_counter() - start)


if __name__ == '__main__':
    main()



