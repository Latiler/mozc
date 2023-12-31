// Copyright 2010-2021, Google Inc.
// All rights reserved.
//
// Redistribution and use in source and binary forms, with or without
// modification, are permitted provided that the following conditions are
// met:
//
//     * Redistributions of source code must retain the above copyright
// notice, this list of conditions and the following disclaimer.
//     * Redistributions in binary form must reproduce the above
// copyright notice, this list of conditions and the following disclaimer
// in the documentation and/or other materials provided with the
// distribution.
//     * Neither the name of Google Inc. nor the names of its
// contributors may be used to endorse or promote products derived from
// this software without specific prior written permission.
//
// THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
// "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
// LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR
// A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT
// OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
// SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
// LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
// DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
// THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
// (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
// OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

#include "dictionary/system/key_expansion_table.h"

#include "testing/gunit.h"

namespace mozc {
namespace dictionary {
namespace {

TEST(KeyExpansionTableTest, Test) {
  KeyExpansionTable table;
  table.Add('b', "d");

  EXPECT_TRUE(table.ExpandKey('a').IsHit('a'));
  EXPECT_FALSE(table.ExpandKey('a').IsHit('b'));
  EXPECT_FALSE(table.ExpandKey('a').IsHit('c'));
  EXPECT_FALSE(table.ExpandKey('a').IsHit('d'));

  EXPECT_FALSE(table.ExpandKey('b').IsHit('a'));
  EXPECT_TRUE(table.ExpandKey('b').IsHit('b'));
  EXPECT_FALSE(table.ExpandKey('b').IsHit('c'));
  EXPECT_TRUE(table.ExpandKey('b').IsHit('d'));

  EXPECT_FALSE(table.ExpandKey('c').IsHit('a'));
  EXPECT_FALSE(table.ExpandKey('c').IsHit('b'));
  EXPECT_TRUE(table.ExpandKey('c').IsHit('c'));
  EXPECT_FALSE(table.ExpandKey('c').IsHit('d'));

  EXPECT_FALSE(table.ExpandKey('d').IsHit('a'));
  EXPECT_FALSE(table.ExpandKey('d').IsHit('b'));
  EXPECT_FALSE(table.ExpandKey('d').IsHit('c'));
  EXPECT_TRUE(table.ExpandKey('d').IsHit('d'));
}

}  // namespace
}  // namespace dictionary
}  // namespace mozc
